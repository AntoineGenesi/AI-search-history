from __future__ import annotations

import hashlib
import importlib
import json
import math
import resource
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .adapters import AdapterReadError, iter_records_for_file
from .embedding_spike import (
    DEFAULT_EMBEDDING_SPIKE_FIXTURE,
    EmbeddingDocument,
    RankedHit,
    load_embedding_fixture,
    rank_current_package_baseline,
    validate_embedding_fixture,
    vectorize_text,
)
from .embedding_store import EmbeddingShardStore, StoreConfig, StoredVectorRecord
from .metric_contract import add_metric_contract, embedding_experiment_metrics
from .sources import (
    Limits,
    SourcePlan,
    build_source_plan,
    cap_hits_from_warnings,
    enumerate_jsonl_files,
    root_label_for_path,
    sanitized_rejected_roots,
    sanitized_selected_roots,
    source_kind_for_path,
)
from .timeutil import parse_now, timezone_from_args


FASTEMBED_MODEL_ID = "BAAI/bge-small-en-v1.5"
DETERMINISTIC_BACKEND_ID = "deterministic_local_fallback"
FASTEMBED_BACKEND_ID = "fastembed_cpu_local"
EXPERIMENT_MODES = {"selected_roots", "date_window_subset", "full_approved_local_history", "smoke_synthetic"}
PROTECTED_FAMILIES = {"exact_quote", "path", "task_slug", "command", "date_window", "no_evidence", "id_like_token"}


@dataclass(frozen=True)
class ExperimentProbe:
    query_text: str
    case_id: str
    case_family: str
    query_source: str
    gold_doc_id: str | None = None


@dataclass(frozen=True)
class ExperimentDocument:
    doc_id: str
    text: str
    timestamp: datetime | None
    role: str
    cwd: str | None
    root_label: str
    parent_doc_id: str | None = None
    chunk_index: int | None = None
    chunk_count: int | None = None

    def as_embedding_document(self) -> EmbeddingDocument:
        return EmbeddingDocument(
            doc_id=self.doc_id,
            text=self.text,
            timestamp=self.timestamp,
            role=self.role,
            cwd=self.cwd,
        )


@dataclass(frozen=True)
class DocumentPlan:
    content_fingerprint: str
    document_count: int
    source_record_count: int
    root_labels: tuple[str, ...]
    expected_doc_ids: tuple[str, ...]
    source_metrics: dict[str, Any]


class MemoryProfiler:
    def __init__(self, *, enabled: bool, started: float) -> None:
        self.enabled = enabled
        self.started = started
        self.samples: list[dict[str, Any]] = []

    def mark(self, phase: str, event: str, **counters: Any) -> None:
        if not self.enabled:
            return
        sample = {
            "phase": phase,
            "event": event,
            "elapsed_ms": _elapsed_ms(self.started),
            "rss_bytes": _current_rss_bytes(),
            "max_rss_bytes": _max_rss_bytes(),
        }
        if tracemalloc.is_tracing():
            current, peak = tracemalloc.get_traced_memory()
            sample["tracemalloc_current_bytes"] = int(current)
            sample["tracemalloc_peak_bytes"] = int(peak)
        safe_counters = {
            key: value
            for key, value in counters.items()
            if isinstance(value, (int, float, bool)) or value is None or isinstance(value, str)
        }
        if safe_counters:
            sample["counters"] = safe_counters
        self.samples.append(sample)

    def public(self, *, python_peak_bytes: int | None = None) -> dict[str, Any]:
        peak_rss = max((int(sample.get("rss_bytes", 0)) for sample in self.samples), default=_current_rss_bytes())
        max_rss = max((int(sample.get("max_rss_bytes", 0)) for sample in self.samples), default=_max_rss_bytes())
        return {
            "enabled": self.enabled,
            "sample_count": len(self.samples),
            "samples": self.samples,
            "peak_observed_rss_bytes": peak_rss,
            "process_max_rss_bytes": max_rss,
            "tracemalloc_python_peak_bytes": python_peak_bytes,
            "tracemalloc_scope": "python_heap_allocations_during_index_build_only",
            "native_memory_note": (
                "tracemalloc excludes native ONNX Runtime/FastEmbed allocations, shared libraries, memory arenas, "
                "and allocator-retained RSS; use rss_bytes/process_max_rss_bytes for native memory behavior."
            ),
        }


def build_embedding_experiment_payload(args) -> dict[str, Any]:
    started = time.perf_counter()
    profiler = MemoryProfiler(enabled=bool(getattr(args, "profile_memory", False)), started=started)
    profiler.mark("experiment", "start")
    tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, tz)
    cwd = Path(args.cwd).expanduser().resolve()
    output_dir = _private_output_dir(args.output_dir, now)
    run_id = _run_id(now)
    limits = Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=args.max_matches,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files,
        hard_cap_bytes=args.hard_cap_bytes,
    )
    base_report = _base_report(args=args, run_id=run_id, limits=limits, started=started)

    config_errors = _configuration_errors(args)
    if config_errors:
        return {"embedding_experiment": _finish_without_scan(base_report, "invalid_config", config_errors, started)}
    if args.experiment_mode not in EXPERIMENT_MODES:
        return {"embedding_experiment": _finish_without_scan(base_report, "invalid_mode", ["invalid_experiment_mode"], started)}
    if not _is_ignored_private_path(output_dir):
        return {
            "embedding_experiment": _finish_without_scan(
                base_report,
                "private_output_rejected",
                ["private_output_dir_must_be_under_dot_private"],
                started,
            )
        }
    if args.experiment_mode == "full_approved_local_history" and not args.approve_full_history:
        return {
            "embedding_experiment": _finish_without_scan(
                base_report,
                "full_history_not_approved",
                ["full_approved_local_history_requires_approve_full_history"],
                started,
            )
        }

    profiler.mark("source_plan", "start")
    plan = _build_plan_for_mode(args=args, cwd=cwd, limits=limits)
    profiler.mark(
        "source_plan",
        "end",
        selected_root_count=len(plan.roots),
        rejected_root_count=len(plan.rejected_roots),
    )
    if args.experiment_mode != "smoke_synthetic":
        base_report["scope"] = _scope_payload(plan, args)
        if not plan.roots:
            warnings = _safe_warnings(plan.warnings + [root.reason for root in plan.rejected_roots] + plan.blind_spots)
            return {
                "embedding_experiment": _finish_without_scan(
                    base_report,
                    "root_rejected" if plan.rejected_roots else "no_roots",
                    warnings or ["no_selected_roots"],
                    started,
                )
            }

    probes, probe_errors = _load_probes(args)
    if probe_errors:
        return {"embedding_experiment": _finish_without_scan(base_report, "invalid_fixture", probe_errors, started)}
    if not probes:
        return {"embedding_experiment": _finish_without_scan(base_report, "no_queries", ["no_query_cases_selected"], started)}

    baseline_mode = str(getattr(args, "baseline_mode", "correctness"))
    streaming_resource_mode = baseline_mode == "skip" and args.experiment_mode != "smoke_synthetic"
    documents: list[ExperimentDocument] | None = None
    if streaming_resource_mode:
        profiler.mark("document_metadata_scan", "start")
        document_plan = _scan_document_plan(args=args, plan=plan, default_tz=tz, profiler=profiler)
        source_metrics = document_plan.source_metrics
        profiler.mark(
            "document_metadata_scan",
            "end",
            document_count=document_plan.document_count,
            source_record_count=document_plan.source_record_count,
            candidate_file_count=int(source_metrics.get("candidate_file_count", 0)),
            candidate_bytes=int(source_metrics.get("candidate_bytes", 0)),
        )
    else:
        profiler.mark("document_load", "start")
        loaded_documents, source_metrics = _load_documents(args=args, plan=plan, default_tz=tz, profiler=profiler)
        documents = loaded_documents
        document_plan = _document_plan_from_documents(documents, source_metrics)
        profiler.mark(
            "document_load",
            "end",
            document_count=len(documents),
            candidate_file_count=int(source_metrics.get("candidate_file_count", 0)),
            candidate_bytes=int(source_metrics.get("candidate_bytes", 0)),
            text_bytes_estimate=sum(len(doc.text.encode("utf-8", errors="replace")) for doc in documents),
        )
    source_metrics = _source_metrics_with_materialization(source_metrics, baseline_mode=baseline_mode, streaming=streaming_resource_mode)
    document_plan = DocumentPlan(
        content_fingerprint=document_plan.content_fingerprint,
        document_count=document_plan.document_count,
        source_record_count=document_plan.source_record_count,
        root_labels=document_plan.root_labels,
        expected_doc_ids=document_plan.expected_doc_ids,
        source_metrics=source_metrics,
    )
    base_report["source_metrics"] = source_metrics
    if document_plan.document_count <= 0:
        return {
            "embedding_experiment": _finish_without_scan(
                base_report,
                "no_documents",
                source_metrics.get("warnings", []) or ["no_documents_loaded"],
                started,
            )
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    profiler.mark("backend_select", "start", backend_requested=args.backend)
    backend_state = _select_backend(args=args, output_dir=output_dir, profiler=profiler)
    profiler.mark("backend_select", "end", backend_selected=backend_state["public"]["selected"])
    onnx_profile = _onnx_profile_status(args=args, backend_state=backend_state)

    build_started = time.perf_counter()
    tracemalloc.start()
    profiler.mark("index_build", "start", document_count=document_plan.document_count)
    document_iterable = (
        _iter_documents(args=args, plan=plan, default_tz=tz, profiler=profiler)
        if streaming_resource_mode
        else iter(documents or [])
    )
    index, index_private_payload = _build_index(
        document_iterable,
        document_plan=document_plan,
        backend_state=backend_state,
        output_dir=output_dir,
        args=args,
        profiler=profiler,
    )
    _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    profiler.mark("index_build", "end", document_count=document_plan.document_count, tracemalloc_peak_bytes=int(peak_bytes))
    tracemalloc.stop()
    index_build_ms = _elapsed_ms(build_started)

    profiler.mark("query_scan", "start", query_case_count=len(probes))
    query_rows, query_latencies = _run_queries(
        probes=probes,
        documents=documents,
        index=index,
        top_k=args.top_k,
        baseline_mode=baseline_mode,
        profiler=profiler,
    )
    profiler.mark("query_scan", "end", query_case_count=len(probes))
    private_artifacts = _write_private_artifacts(
        output_dir=output_dir,
        run_id=run_id,
        index_payload=index_private_payload,
        document_plan=document_plan,
        args=args,
        profiler=profiler,
    )
    index_size_bytes = int(private_artifacts.get("index_size_bytes", 0))
    cap_labels = sorted(
        set(source_metrics.get("cap_hit_labels", []))
        | ({"max_records"} if source_metrics.get("records_cap_hit") else set())
        | ({"max_index_disk"} if index_size_bytes > args.max_index_disk else set())
    )

    aggregate = _aggregate_rows(
        rows=query_rows,
        started=started,
        source_metrics=source_metrics,
        index_build_ms=index_build_ms,
        peak_memory_bytes=peak_bytes,
        index_size_bytes=index_size_bytes,
        query_latencies=query_latencies,
        cap_labels=cap_labels,
        refresh_metrics=index_private_payload["refresh_metrics"],
    )
    profiler.mark("report_generation", "start")
    memory_profile = profiler.public(python_peak_bytes=peak_bytes)
    report = {
        **base_report,
        "status": "capped" if cap_labels else "ok",
        "backend": backend_state["public"],
        "onnx_profile": onnx_profile,
        "memory_profile": memory_profile,
        "root_cause_profile": _root_cause_profile(memory_profile),
        "index_metrics": aggregate["index_metrics"],
        "query_metrics": aggregate["query_metrics"],
        "refresh_metrics": index_private_payload["refresh_metrics"]["public"],
        "storage_metrics": index_private_payload["storage_metrics"],
        "batching": index_private_payload["batching"],
        "chunking": index_private_payload["chunking"],
        "baseline_comparison": _baseline_comparison_payload(baseline_mode),
        "streaming_enumeration": _streaming_enumeration_payload(streaming_resource_mode, source_metrics),
        "private_artifacts": private_artifacts,
        "comparison": {
            "baseline": "current_jsonl_exact_fuzzy_local_semantic",
            "embedding_style": backend_state["public"]["selected"],
            "case_families": sorted({row["case_family"] for row in query_rows}),
            "source_ref_recovery": "embedding candidates remain tied to JSONL parser records internally; committed output uses doc labels only",
        },
        "embedding_recall_gate": _embedding_recall_gate(query_rows, aggregate),
        "rows": query_rows,
        "aggregate": aggregate,
        "privacy_contract": _privacy_contract(),
        "production_status": "experimental_unpromoted_not_default",
        "default_live_search_changed": False,
    }
    profiler.mark("report_generation", "end")
    report["memory_profile"] = profiler.public(python_peak_bytes=peak_bytes)
    report["root_cause_profile"] = _root_cause_profile(report["memory_profile"])
    _add_metric_sections(report)
    return {"embedding_experiment": report}


def _build_plan_for_mode(args, cwd: Path, limits: Limits) -> SourcePlan:
    if args.experiment_mode == "smoke_synthetic":
        return SourcePlan(limits=limits)
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"
    claude_home = Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude"
    roots = [Path(item).expanduser() for item in args.root] if args.root else None
    if args.experiment_mode in {"selected_roots", "date_window_subset"} and not roots:
        return SourcePlan(limits=limits, blind_spots=["No explicit approved roots supplied for this experiment mode."])
    return build_source_plan(
        cwd=cwd,
        codex_home=codex_home,
        claude_home=claude_home,
        roots=roots,
        allow_arbitrary_root=args.allow_arbitrary_root,
        limits=limits,
    )


def _configuration_errors(args) -> list[str]:
    errors: list[str] = []
    if args.fastembed_batch_size < 1:
        errors.append("fastembed_batch_size_must_be_positive")
    if args.shard_size < 1:
        errors.append("shard_size_must_be_positive")
    if args.chunk_size_chars < 0:
        errors.append("chunk_size_chars_must_not_be_negative")
    if args.chunk_overlap_chars < 0:
        errors.append("chunk_overlap_chars_must_not_be_negative")
    if args.chunk_size_chars and args.chunk_overlap_chars >= args.chunk_size_chars:
        errors.append("chunk_overlap_chars_must_be_less_than_chunk_size_chars")
    return errors


def _chunking_config(args) -> dict[str, Any]:
    return {
        "enabled": bool(args.chunk_size_chars),
        "chunk_size_chars": int(args.chunk_size_chars),
        "chunk_overlap_chars": int(args.chunk_overlap_chars),
        "private_metadata_links_chunks_to_source_records": bool(args.chunk_size_chars),
        "raw_text_committed": False,
    }


def _load_documents(args, plan: SourcePlan, default_tz, profiler: MemoryProfiler | None = None) -> tuple[list[ExperimentDocument], dict[str, Any]]:
    if args.experiment_mode == "smoke_synthetic":
        payload = load_embedding_fixture(Path(args.fixture).expanduser())
        errors = validate_embedding_fixture(payload)
        if errors:
            return [], {"status": "invalid_fixture", "warnings": [f"fixture validation failed: {error}" for error in errors[:5]]}
        documents: list[ExperimentDocument] = []
        source_items = list(payload.get("documents", []))[: args.max_records]
        for item in source_items:
            documents.extend(
                _documents_for_text(
                    base_doc_id=str(item["doc_id"]),
                    text=str(item["text"]),
                    timestamp=_parse_fixture_timestamp(item.get("timestamp")),
                    role=str(item.get("role") or "assistant"),
                    cwd=item.get("cwd"),
                    root_label="synthetic_fixture",
                    args=args,
                )
            )
        return documents, {
            "status": "ok",
            "data_source": "committed_sanitized_synthetic_fixture",
            "selected_root_count": 0,
            "candidate_file_count": 1,
            "candidate_bytes": 0,
            "record_count": len(source_items),
            "embedding_document_count": len(documents),
            "chunk_count": max(0, len(documents) - len(source_items)),
            "skipped_records": 0,
            "records_cap_hit": len(payload.get("documents", [])) > len(source_items),
            "cap_hit_labels": ["max_records"] if len(payload.get("documents", [])) > len(source_items) else [],
            "warnings": [],
        }

    documents: list[ExperimentDocument] = []
    warnings = list(plan.warnings)
    candidate_file_count = 0
    candidate_bytes = 0
    skipped_records = 0
    source_record_count = 0
    started = time.perf_counter()
    window_start = _parse_window(args.window_start)
    window_end = _parse_window(args.window_end)
    records_cap_hit = False

    for path, size, enum_warnings in enumerate_jsonl_files(plan):
        if profiler:
            profiler.mark(
                "document_enumeration",
                "file_selected",
                candidate_file_count=candidate_file_count + 1,
                candidate_bytes=candidate_bytes + size,
                document_count=len(documents),
            )
        warnings.extend(enum_warnings)
        candidate_file_count += 1
        candidate_bytes += size
        source_kind = source_kind_for_path(path, plan)
        root_label = root_label_for_path(path, plan)
        try:
            for record in iter_records_for_file(path, source_kind, default_tz):
                if time.perf_counter() - started > plan.limits.max_elapsed:
                    warnings.append("Stopped loading records at max elapsed cap.")
                    break
                if args.experiment_mode == "date_window_subset" and not _in_window(record.timestamp, window_start, window_end):
                    skipped_records += 1
                    continue
                if not record.text:
                    skipped_records += 1
                    continue
                if source_record_count >= args.max_records:
                    records_cap_hit = True
                    warnings.append("Stopped loading records at max records cap.")
                    break
                source_record_count += 1
                documents.extend(
                    _documents_for_text(
                        base_doc_id=f"doc_{source_record_count:06d}",
                        text=record.text,
                        timestamp=record.timestamp,
                        role=record.role,
                        cwd=record.cwd,
                        root_label=root_label,
                        args=args,
                    )
                )
                if profiler and len(documents) in {1, 10, 50, 100, 250, 500, 1000}:
                    profiler.mark("record_materialization", "record_checkpoint", document_count=len(documents))
        except AdapterReadError as exc:
            warnings.append(_sanitize_exception(exc))
        if records_cap_hit:
            break

    cap_hits = cap_hits_from_warnings(warnings)
    cap_labels = [key for key, hit in cap_hits.items() if hit]
    if records_cap_hit:
        cap_labels.append("max_records")
    return documents, {
        "status": "capped" if cap_labels else "ok",
        "data_source": "live_local_roots",
        "selected_root_count": len(plan.roots),
        "candidate_file_count": candidate_file_count,
        "candidate_bytes": candidate_bytes,
        "record_count": source_record_count,
        "embedding_document_count": len(documents),
        "chunk_count": max(0, len(documents) - source_record_count),
        "skipped_records": skipped_records,
        "records_cap_hit": records_cap_hit,
        "cap_hit_labels": sorted(set(cap_labels)),
        "warnings": _safe_warnings(warnings),
    }


def _iter_documents(args, plan: SourcePlan, default_tz, profiler: MemoryProfiler | None = None) -> Iterable[ExperimentDocument]:
    if args.experiment_mode == "smoke_synthetic":
        payload = load_embedding_fixture(Path(args.fixture).expanduser())
        for item in list(payload.get("documents", []))[: args.max_records]:
            yield from _documents_for_text(
                base_doc_id=str(item["doc_id"]),
                text=str(item["text"]),
                timestamp=_parse_fixture_timestamp(item.get("timestamp")),
                role=str(item.get("role") or "assistant"),
                cwd=item.get("cwd"),
                root_label="synthetic_fixture",
                args=args,
            )
        return

    source_record_count = 0
    started = time.perf_counter()
    window_start = _parse_window(args.window_start)
    window_end = _parse_window(args.window_end)
    for path, _size, _enum_warnings in enumerate_jsonl_files(plan):
        source_kind = source_kind_for_path(path, plan)
        root_label = root_label_for_path(path, plan)
        try:
            for record in iter_records_for_file(path, source_kind, default_tz):
                if time.perf_counter() - started > plan.limits.max_elapsed:
                    return
                if args.experiment_mode == "date_window_subset" and not _in_window(record.timestamp, window_start, window_end):
                    continue
                if not record.text:
                    continue
                if source_record_count >= args.max_records:
                    return
                source_record_count += 1
                yield from _documents_for_text(
                    base_doc_id=f"doc_{source_record_count:06d}",
                    text=record.text,
                    timestamp=record.timestamp,
                    role=record.role,
                    cwd=record.cwd,
                    root_label=root_label,
                    args=args,
                )
                if profiler and source_record_count in {1, 10, 50, 100, 250, 500, 1000}:
                    profiler.mark("streaming_document_enumeration", "record_checkpoint", source_record_count=source_record_count)
        except AdapterReadError:
            continue


def _scan_document_plan(args, plan: SourcePlan, default_tz, profiler: MemoryProfiler | None = None) -> DocumentPlan:
    if args.experiment_mode == "smoke_synthetic":
        documents, source_metrics = _load_documents(args=args, plan=plan, default_tz=default_tz, profiler=profiler)
        return _document_plan_from_documents(documents, source_metrics)

    digest = hashlib.sha256()
    expected_doc_ids: list[str] = []
    root_labels: set[str] = set()
    warnings = list(plan.warnings)
    candidate_file_count = 0
    candidate_bytes = 0
    skipped_records = 0
    source_record_count = 0
    embedding_document_count = 0
    started = time.perf_counter()
    window_start = _parse_window(args.window_start)
    window_end = _parse_window(args.window_end)
    records_cap_hit = False

    for path, size, enum_warnings in enumerate_jsonl_files(plan):
        warnings.extend(enum_warnings)
        candidate_file_count += 1
        candidate_bytes += size
        source_kind = source_kind_for_path(path, plan)
        root_label = root_label_for_path(path, plan)
        root_labels.add(root_label)
        try:
            for record in iter_records_for_file(path, source_kind, default_tz):
                if time.perf_counter() - started > plan.limits.max_elapsed:
                    warnings.append("Stopped loading records at max elapsed cap.")
                    break
                if args.experiment_mode == "date_window_subset" and not _in_window(record.timestamp, window_start, window_end):
                    skipped_records += 1
                    continue
                if not record.text:
                    skipped_records += 1
                    continue
                if source_record_count >= args.max_records:
                    records_cap_hit = True
                    warnings.append("Stopped loading records at max records cap.")
                    break
                source_record_count += 1
                for doc in _documents_for_text(
                    base_doc_id=f"doc_{source_record_count:06d}",
                    text=record.text,
                    timestamp=record.timestamp,
                    role=record.role,
                    cwd=record.cwd,
                    root_label=root_label,
                    args=args,
                ):
                    embedding_document_count += 1
                    expected_doc_ids.append(doc.doc_id)
                    _update_content_fingerprint(digest, doc)
                if profiler and source_record_count in {1, 10, 50, 100, 250, 500, 1000}:
                    profiler.mark(
                        "document_metadata_scan",
                        "record_checkpoint",
                        source_record_count=source_record_count,
                        embedding_document_count=embedding_document_count,
                    )
        except AdapterReadError as exc:
            warnings.append(_sanitize_exception(exc))
        if records_cap_hit:
            break

    cap_hits = cap_hits_from_warnings(warnings)
    cap_labels = [key for key, hit in cap_hits.items() if hit]
    if records_cap_hit:
        cap_labels.append("max_records")
    source_metrics = {
        "status": "capped" if cap_labels else "ok",
        "data_source": "live_local_roots",
        "selected_root_count": len(plan.roots),
        "candidate_file_count": candidate_file_count,
        "candidate_bytes": candidate_bytes,
        "record_count": source_record_count,
        "embedding_document_count": embedding_document_count,
        "chunk_count": max(0, embedding_document_count - source_record_count),
        "skipped_records": skipped_records,
        "records_cap_hit": records_cap_hit,
        "cap_hit_labels": sorted(set(cap_labels)),
        "warnings": _safe_warnings(warnings),
    }
    return DocumentPlan(
        content_fingerprint=f"sha256:{digest.hexdigest()}",
        document_count=embedding_document_count,
        source_record_count=source_record_count,
        root_labels=tuple(sorted(root_labels)),
        expected_doc_ids=tuple(expected_doc_ids),
        source_metrics=source_metrics,
    )


def _document_plan_from_documents(documents: list[ExperimentDocument], source_metrics: dict[str, Any]) -> DocumentPlan:
    return DocumentPlan(
        content_fingerprint=_content_fingerprint(documents),
        document_count=len(documents),
        source_record_count=int(source_metrics.get("record_count", len(documents))),
        root_labels=tuple(sorted({doc.root_label for doc in documents})),
        expected_doc_ids=tuple(doc.doc_id for doc in documents),
        source_metrics=dict(source_metrics),
    )


def _source_metrics_with_materialization(source_metrics: dict[str, Any], *, baseline_mode: str, streaming: bool) -> dict[str, Any]:
    metrics = dict(source_metrics)
    metrics["document_enumeration_mode"] = "streaming_two_pass_index_build" if streaming else "materialized_document_list"
    metrics["documents_retained_for_index_build"] = not streaming
    metrics["baseline_mode"] = baseline_mode
    metrics["baseline_documents_materialized"] = baseline_mode == "correctness"
    metrics["correctness_comparison_available"] = baseline_mode == "correctness"
    return metrics


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


def _streaming_enumeration_payload(streaming: bool, source_metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": streaming,
        "mode": source_metrics.get("document_enumeration_mode"),
        "documents_retained_for_index_build": source_metrics.get("documents_retained_for_index_build"),
        "baseline_documents_materialized": source_metrics.get("baseline_documents_materialized"),
        "scope": "live_selected_roots_only" if streaming else "default_materialized_or_synthetic",
    }


def _documents_for_text(
    *,
    base_doc_id: str,
    text: str,
    timestamp: datetime | None,
    role: str,
    cwd: str | None,
    root_label: str,
    args,
) -> list[ExperimentDocument]:
    chunks = _chunk_text(text, args.chunk_size_chars, args.chunk_overlap_chars)
    if len(chunks) == 1:
        return [
            ExperimentDocument(
                doc_id=base_doc_id,
                text=chunks[0],
                timestamp=timestamp,
                role=role,
                cwd=cwd,
                root_label=root_label,
                parent_doc_id=base_doc_id if args.chunk_size_chars else None,
                chunk_index=1 if args.chunk_size_chars else None,
                chunk_count=1 if args.chunk_size_chars else None,
            )
        ]
    return [
        ExperimentDocument(
            doc_id=f"{base_doc_id}_chunk_{index + 1:03d}",
            text=chunk,
            timestamp=timestamp,
            role=role,
            cwd=cwd,
            root_label=root_label,
            parent_doc_id=base_doc_id,
            chunk_index=index + 1,
            chunk_count=len(chunks),
        )
        for index, chunk in enumerate(chunks)
    ]


def _chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if chunk_size <= 0 or len(text) <= chunk_size:
        return [text]
    stride = max(1, chunk_size - chunk_overlap)
    chunks: list[str] = []
    for start in range(0, len(text), stride):
        chunk = text[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(text):
            break
    return chunks or [text]


def _select_backend(args, output_dir: Path, profiler: MemoryProfiler | None = None) -> dict[str, Any]:
    fastembed_state = _fastembed_state(args, output_dir, profiler=profiler)
    if args.backend == "fastembed" and fastembed_state.get("model") is not None:
        return {
            "kind": FASTEMBED_BACKEND_ID,
            "model": fastembed_state["model"],
            "public": _backend_public(args.backend, FASTEMBED_BACKEND_ID, fastembed_state, fallback_used=False),
        }
    if args.backend == "auto" and fastembed_state.get("model") is not None:
        return {
            "kind": FASTEMBED_BACKEND_ID,
            "model": fastembed_state["model"],
            "public": _backend_public(args.backend, FASTEMBED_BACKEND_ID, fastembed_state, fallback_used=False),
        }
    return {
        "kind": DETERMINISTIC_BACKEND_ID,
        "model": None,
        "public": _backend_public(args.backend, DETERMINISTIC_BACKEND_ID, fastembed_state, fallback_used=args.backend != "deterministic"),
    }


def _fastembed_state(args, output_dir: Path, profiler: MemoryProfiler | None = None) -> dict[str, Any]:
    if args.backend == "deterministic":
        return _fastembed_public_state(args, "not_requested", download_attempted=False)
    try:
        module = importlib.import_module("fastembed")
    except ImportError:
        return _fastembed_public_state(args, "fastembed_local_missing_dependency", download_attempted=False)
    text_embedding = getattr(module, "TextEmbedding", None)
    if text_embedding is None:
        return _fastembed_public_state(args, "fastembed_local_missing_dependency", download_attempted=False)
    cache_dir = _fastembed_cache_dir(args, output_dir)
    if not _is_ignored_private_path(cache_dir):
        return _fastembed_public_state(
            args,
            "fastembed_local_cache_rejected",
            download_attempted=False,
            failure_label="model_cache_dir_must_be_under_dot_private",
        )
    cache_dir.mkdir(parents=True, exist_ok=True)
    local_files_only = not bool(args.allow_model_download)
    try:
        if profiler:
            profiler.mark("fastembed_model_session", "constructor_start")
        model = text_embedding(model_name=args.fastembed_model, cache_dir=str(cache_dir), local_files_only=local_files_only)
        if profiler:
            profiler.mark("fastembed_model_session", "constructor_end")
    except TypeError:
        if not args.allow_model_download:
            return _fastembed_public_state(
                args,
                "fastembed_local_model_unavailable",
                download_attempted=False,
                failure_label="constructor_without_local_files_only",
            )
        try:
            if profiler:
                profiler.mark("fastembed_model_session", "constructor_retry_start")
            model = text_embedding(model_name=args.fastembed_model, cache_dir=str(cache_dir))
            if profiler:
                profiler.mark("fastembed_model_session", "constructor_retry_end")
        except Exception as exc:  # pragma: no cover - depends on optional local model state
            return _fastembed_public_state(
                args,
                "fastembed_local_model_unavailable",
                download_attempted=True,
                failure_label=_sanitize_exception(exc),
                constructor_local_files_only_control=False,
            )
        return {
            **_fastembed_public_state(
                args,
                "fastembed_local_ready",
                download_attempted=True,
                constructor_local_files_only_control=False,
            ),
            "model": model,
        }
    except Exception as exc:  # pragma: no cover - depends on optional local model state
        return _fastembed_public_state(
            args,
            "fastembed_local_model_unavailable",
            download_attempted=bool(args.allow_model_download),
            failure_label=_sanitize_exception(exc),
        )
    return {
        **_fastembed_public_state(
            args,
            "fastembed_local_ready",
            download_attempted=bool(args.allow_model_download),
        ),
        "model": model,
    }


def _fastembed_cache_dir(args, output_dir: Path) -> Path:
    if args.fastembed_cache_dir:
        return Path(args.fastembed_cache_dir).expanduser().resolve()
    return (output_dir / "model-cache" / "fastembed").resolve()


def _fastembed_public_state(
    args,
    status: str,
    *,
    download_attempted: bool,
    failure_label: str | None = None,
    constructor_local_files_only_control: bool = True,
) -> dict[str, Any]:
    state = {
        "status": status,
        "model_id": args.fastembed_model,
        "download_attempted": download_attempted,
        "model_download_allowed": bool(args.allow_model_download),
        "local_files_only": not bool(args.allow_model_download),
        "cache_scope": "ignored_private_model_cache",
        "constructor_local_files_only_control": constructor_local_files_only_control,
        "private_text_upload": False,
        "remote_embedding_api": False,
    }
    if failure_label:
        state["failure_label"] = failure_label
    return state


def _backend_public(requested: str, selected: str, fastembed_state: dict[str, Any], *, fallback_used: bool) -> dict[str, Any]:
    public_fastembed = {
        key: value
        for key, value in fastembed_state.items()
        if key
        in {
            "status",
            "model_id",
            "download_attempted",
            "model_download_allowed",
            "local_files_only",
            "cache_scope",
            "constructor_local_files_only_control",
            "private_text_upload",
            "remote_embedding_api",
            "failure_label",
        }
    }
    return {
        "requested": requested,
        "selected": selected,
        "fastembed": public_fastembed,
        "fallback_used": fallback_used,
        "deterministic_fallback": {
            "status": "deterministic_local_fallback_ready",
            "network_required": False,
            "model_download_required": False,
            "production_quality_evidence": False,
        },
    }


def _build_index(
    documents: Iterable[ExperimentDocument],
    *,
    document_plan: DocumentPlan,
    backend_state: dict[str, Any],
    output_dir: Path,
    args,
    profiler: MemoryProfiler | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    store = EmbeddingShardStore(output_dir)
    backend = backend_state["kind"]
    model_id = args.fastembed_model if backend == FASTEMBED_BACKEND_ID else "deterministic_dense_hash_v1"
    config = StoreConfig(
        backend=backend,
        model_id=model_id,
        content_fingerprint=document_plan.content_fingerprint,
        document_count=document_plan.document_count,
        shard_size=args.shard_size,
        batch_size=args.fastembed_batch_size,
        chunking=_chunking_config(args),
    )
    expected_doc_ids = list(document_plan.expected_doc_ids)
    profiler.mark("refresh_manifest", "start", document_count=document_plan.document_count) if profiler else None
    _previous_manifest, reusable_shards, reusable_count = store.reusable_prefix(config, expected_doc_ids)
    profiler.mark(
        "refresh_manifest",
        "end",
        document_count=document_plan.document_count,
        reusable_shard_count=len(reusable_shards),
        reusable_record_count=reusable_count,
    ) if profiler else None

    shards = list(reusable_shards)
    shard_buffer: list[StoredVectorRecord] = []
    embedded_count = 0
    batch_count = 0
    raw_vector_bytes = 0
    next_shard_index = len(shards)
    batch_size = args.fastembed_batch_size
    missing_documents = _skip_documents(documents, reusable_count)
    for batch_index, batch in enumerate(_document_batches(missing_documents, batch_size), start=1):
        batch_count += 1
        vectors = _embed_document_batch(batch, backend_state, batch_index=batch_index, profiler=profiler)
        for doc, vector in zip(batch, vectors):
            raw_vector_bytes += len(vector) * 4
            embedded_count += 1
            shard_buffer.append(
                StoredVectorRecord(
                    doc_id=doc.doc_id,
                    vector=vector,
                    root_label=doc.root_label,
                    parent_doc_id=doc.parent_doc_id,
                    chunk_index=doc.chunk_index,
                    chunk_count=doc.chunk_count,
                )
            )
            if len(shard_buffer) >= args.shard_size:
                shard = store.write_shard(next_shard_index, shard_buffer)
                shards.append(shard)
                next_shard_index += 1
                shard_buffer = []
                store.write_manifest(config, shards, complete=False)
                if profiler:
                    profiler.mark("vector_shard_write", "shard_written", shard_count=len(shards), record_count=shard["record_count"])
    if shard_buffer:
        shard = store.write_shard(next_shard_index, shard_buffer)
        shards.append(shard)
        store.write_manifest(config, shards, complete=False)
        if profiler:
            profiler.mark("vector_shard_write", "shard_written", shard_count=len(shards), record_count=shard["record_count"])
    store.write_manifest(config, shards, complete=sum(int(shard.get("record_count") or 0) for shard in shards) == document_plan.document_count)
    storage_metrics = store.storage_metrics()
    refresh_metrics = _refresh_metrics_from_store(
        reusable_count=reusable_count,
        document_count=document_plan.document_count,
        previous_manifest_found=_previous_manifest is not None,
    )
    if profiler:
        profiler.mark(
            "vector_retention",
            "end",
            vector_count=embedded_count,
            reused_vector_count=reusable_count,
            raw_float32_bytes_estimate=raw_vector_bytes,
        )
    index = {"backend": backend, "model": backend_state.get("model"), "store": store}
    private_payload = {
        "schema_version": 1,
        "backend": backend,
        "document_ids": expected_doc_ids,
        "raw_text_committed": False,
        "storage_metrics": storage_metrics,
        "refresh_metrics": refresh_metrics,
        "batching": {
            "fastembed_batch_size": args.fastembed_batch_size,
            "shard_size": args.shard_size,
            "document_batches_embedded": batch_count,
            "documents_embedded": embedded_count,
            "documents_reused": reusable_count,
            "raw_float32_vector_bytes_written": raw_vector_bytes,
        },
        "chunking": _chunking_config(args),
    }
    return index, private_payload


def _embed_fastembed(
    model: Any,
    texts: list[str],
    profiler: MemoryProfiler | None = None,
    phase: str = "fastembed_batch",
    batch_index: int | None = None,
) -> list[list[float]]:
    vectors = []
    if profiler:
        counters = {"input_count": len(texts)}
        if batch_index is not None:
            counters["batch_index"] = batch_index
        profiler.mark(phase, "embed_generator_start", **counters)
    for vector in model.embed(texts):
        dense = [float(value) for value in vector]
        length = math.sqrt(sum(value * value for value in dense))
        vectors.append([round(value / length, 8) for value in dense] if length else dense)
        if profiler and len(vectors) in {1, 10, 50, 100, 250, 500, 1000}:
            counters = {"vector_count": len(vectors), "vector_dim": len(dense)}
            if batch_index is not None:
                counters["batch_index"] = batch_index
            profiler.mark(phase, "vector_checkpoint", **counters)
    if profiler:
        vector_dim = len(vectors[0]) if vectors else 0
        profiler.mark(
            phase,
            "embed_generator_end",
            input_count=len(texts),
            vector_count=len(vectors),
            vector_dim=vector_dim,
            raw_float32_bytes_estimate=len(vectors) * vector_dim * 4,
            batch_index=batch_index,
        )
    return vectors


def _embed_document_batch(
    documents: list[ExperimentDocument],
    backend_state: dict[str, Any],
    *,
    batch_index: int,
    profiler: MemoryProfiler | None = None,
) -> list[list[float]]:
    if backend_state["kind"] == FASTEMBED_BACKEND_ID:
        texts = [doc.text for doc in documents]
        if profiler:
            profiler.mark(
                "text_materialization",
                "batch",
                batch_index=batch_index,
                document_count=len(documents),
                text_bytes_estimate=sum(len(text.encode("utf-8", errors="replace")) for text in texts),
            )
        return _embed_fastembed(
            backend_state["model"],
            texts,
            profiler=profiler,
            phase="fastembed_document_batch",
            batch_index=batch_index,
        )
    return [_dense_deterministic_vector(doc.text) for doc in documents]


def _document_batches(documents: Iterable[ExperimentDocument], batch_size: int) -> Iterable[list[ExperimentDocument]]:
    batch: list[ExperimentDocument] = []
    for document in documents:
        batch.append(document)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _skip_documents(documents: Iterable[ExperimentDocument], skip_count: int) -> Iterable[ExperimentDocument]:
    for index, document in enumerate(documents):
        if index < skip_count:
            continue
        yield document


def _dense_deterministic_vector(text: str, dims: int = 64) -> list[float]:
    vector = [0.0] * dims
    for feature, weight in vectorize_text(text).items():
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign * float(weight)
    length = math.sqrt(sum(value * value for value in vector))
    if not length:
        return vector
    return [round(value / length, 8) for value in vector]


def _run_queries(
    *,
    probes: list[ExperimentProbe],
    documents: list[ExperimentDocument] | None,
    index: dict[str, Any],
    top_k: int,
    baseline_mode: str,
    profiler: MemoryProfiler | None = None,
) -> tuple[list[dict[str, Any]], list[int]]:
    rows: list[dict[str, Any]] = []
    latencies: list[int] = []
    embedding_documents: list[EmbeddingDocument] | None = None
    if baseline_mode == "correctness" and documents is not None and profiler:
        profiler.mark("query_baseline_materialization", "start", document_count=len(documents))
    if baseline_mode == "correctness" and documents is not None:
        embedding_documents = [doc.as_embedding_document() for doc in documents]
    if baseline_mode == "correctness" and documents is not None and profiler:
        profiler.mark("query_baseline_materialization", "end", document_count=len(embedding_documents))
    for probe in probes:
        started = time.perf_counter()
        embedding_hits = _rank_embedding(probe.query_text, index, top_k=top_k, profiler=profiler)
        latency_ms = _elapsed_ms(started)
        latencies.append(latency_ms)
        baseline_hits = (
            rank_current_package_baseline(probe.query_text, embedding_documents)[:top_k]
            if embedding_documents is not None
            else None
        )
        row = _row_for_probe(probe, embedding_hits, baseline_hits, latency_ms, baseline_mode=baseline_mode)
        rows.append(row)
    return rows, latencies


def _rank_embedding(query: str, index: dict[str, Any], *, top_k: int, profiler: MemoryProfiler | None = None) -> list[RankedHit]:
    store = index.get("store")
    if not isinstance(store, EmbeddingShardStore):
        return []
    if index.get("backend") == FASTEMBED_BACKEND_ID:
        model = index.get("model")
        if model is None:
            return []
        if profiler:
            profiler.mark("query_embedding", "start", backend=FASTEMBED_BACKEND_ID)
        query_vectors = _embed_fastembed(model, [query], profiler=profiler, phase="fastembed_query_batch", batch_index=1)
        query_vector = query_vectors[0] if query_vectors else []
        if profiler:
            profiler.mark("query_embedding", "end", backend=FASTEMBED_BACKEND_ID, vector_dim=len(query_vector))
        return store.rank_dense(query_vector, top_k=top_k, profiler=profiler)
    if profiler:
        profiler.mark("query_embedding", "start", backend=DETERMINISTIC_BACKEND_ID)
    query_vector = _dense_deterministic_vector(query)
    if profiler:
        profiler.mark("query_embedding", "end", backend=DETERMINISTIC_BACKEND_ID, vector_dim=len(query_vector))
    return store.rank_dense(query_vector, top_k=top_k, profiler=profiler)


def _row_for_probe(
    probe: ExperimentProbe,
    embedding_hits: list[RankedHit],
    baseline_hits: list[RankedHit] | None,
    query_latency_ms: int,
    *,
    baseline_mode: str,
) -> dict[str, Any]:
    embedding_rank = _gold_rank(embedding_hits, probe.gold_doc_id)
    baseline_rank = _gold_rank(baseline_hits or [], probe.gold_doc_id) if baseline_hits is not None else None
    baseline_available = baseline_hits is not None
    return {
        "case_id": probe.case_id,
        "case_family": probe.case_family,
        "query_source": probe.query_source,
        "candidate_count": len(embedding_hits),
        "baseline_candidate_count": len(baseline_hits or []),
        "query_latency_ms": query_latency_ms,
        "embedding": _strategy_metrics(embedding_hits, probe.gold_doc_id),
        "baseline": _strategy_metrics(baseline_hits or [], probe.gold_doc_id) if baseline_available else _skipped_baseline_metrics(),
        "baseline_comparison": {
            "mode": baseline_mode,
            "status": "available" if baseline_available else "skipped_for_resource_measurement",
            "correctness_comparison_available": baseline_available,
        },
        "first_pass_delta": _first_pass_delta(embedding_rank, baseline_rank) if baseline_available else None,
        "protected_regression": _protected_regression(probe.case_family, embedding_rank, baseline_rank) if baseline_available else None,
        "gold_available": probe.gold_doc_id is not None,
        "top_embedding_doc_labels": [hit.doc_id for hit in embedding_hits[:3]],
        "top_baseline_doc_labels": [hit.doc_id for hit in (baseline_hits or [])[:3]],
    }


def _strategy_metrics(hits: list[RankedHit], gold_doc_id: str | None) -> dict[str, Any]:
    rank = _gold_rank(hits, gold_doc_id)
    return {
        "first_pass_hit": rank == 1 if gold_doc_id else None,
        "gold_rank": rank,
        "recall_at_1": 1.0 if rank is not None and rank <= 1 else (None if not gold_doc_id else 0.0),
        "recall_at_3": 1.0 if rank is not None and rank <= 3 else (None if not gold_doc_id else 0.0),
        "recall_at_10": 1.0 if rank is not None and rank <= 10 else (None if not gold_doc_id else 0.0),
    }


def _skipped_baseline_metrics() -> dict[str, Any]:
    return {
        "status": "skipped_for_resource_measurement",
        "first_pass_hit": None,
        "gold_rank": None,
        "recall_at_1": None,
        "recall_at_3": None,
        "recall_at_10": None,
    }


def _write_private_artifacts(
    *,
    output_dir: Path,
    run_id: str,
    index_payload: dict[str, Any],
    document_plan: DocumentPlan,
    args,
    profiler: MemoryProfiler | None = None,
) -> dict[str, Any]:
    manifest_path = output_dir / "resume-manifest.private.json"
    storage_metrics = index_payload["storage_metrics"]
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "experiment_mode": args.experiment_mode,
        "backend": index_payload.get("backend"),
        "document_count": document_plan.document_count,
        "root_labels": list(document_plan.root_labels),
        "content_fingerprint": document_plan.content_fingerprint,
        "refresh": index_payload["refresh_metrics"]["private"],
        "storage": storage_metrics,
        "batching": index_payload["batching"],
        "chunking": index_payload["chunking"],
        "baseline_mode": getattr(args, "baseline_mode", "correctness"),
        "raw_text_committed": False,
    }
    if profiler:
        profiler.mark("json_serialization", "manifest_start")
    manifest_json = json.dumps(manifest, indent=2, sort_keys=True)
    if profiler:
        profiler.mark("json_serialization", "manifest_end", json_bytes=len(manifest_json.encode("utf-8")))
    profiler.mark("private_write", "manifest_start") if profiler else None
    manifest_path.write_text(manifest_json, encoding="utf-8")
    profiler.mark("private_write", "manifest_end") if profiler else None
    return {
        "scope": "ignored_private_output",
        "index_written": False,
        "resume_manifest_written": True,
        "sharded_vector_manifest_written": storage_metrics.get("manifest_found", False),
        "binary_vector_shards_written": storage_metrics.get("shard_count", 0) > 0,
        "raw_text_written": False,
        "index_size_bytes": int(storage_metrics.get("total_bytes", 0)),
        "vector_shard_count": storage_metrics.get("shard_count", 0),
        "vector_bytes": storage_metrics.get("vector_bytes", 0),
        "metadata_bytes": storage_metrics.get("metadata_bytes", 0),
        "sharded_manifest_label": "private_embedding_shards_manifest_json",
        "vector_shard_label": "private_embedding_vector_shards_f32",
        "resume_manifest_label": "private_resume_manifest_json",
    }


def _refresh_metrics_from_store(*, reusable_count: int, document_count: int, previous_manifest_found: bool) -> dict[str, Any]:
    no_change = document_count > 0 and reusable_count == document_count
    if no_change:
        public_status = "no_change"
    elif reusable_count:
        public_status = "partial_resume"
    elif previous_manifest_found:
        public_status = "changed_or_unusable_manifest"
    else:
        public_status = "no_previous_manifest"
    private = {
        "previous_manifest_found": previous_manifest_found,
        "reusable_record_count": reusable_count,
        "document_count": document_count,
        "no_change": no_change,
    }
    return {
        "private": private,
        "public": {
            "status": public_status,
            "elapsed_ms": 0,
            "scope": "ignored_private_shard_manifest",
            "reusable_record_count": reusable_count,
            "document_count": document_count,
        },
    }


def _aggregate_rows(
    *,
    rows: list[dict[str, Any]],
    started: float,
    source_metrics: dict[str, Any],
    index_build_ms: int,
    peak_memory_bytes: int,
    index_size_bytes: int,
    query_latencies: list[int],
    cap_labels: list[str],
    refresh_metrics: dict[str, Any],
) -> dict[str, Any]:
    gold_rows = [row for row in rows if row.get("gold_available")]
    return {
        "overall_status": "capped" if cap_labels else "ok",
        "total_elapsed_ms": _elapsed_ms(started),
        "source_metrics": source_metrics,
        "index_metrics": {
            "index_build_time_ms": index_build_ms,
            "peak_memory_bytes": peak_memory_bytes,
            "disk_index_size_bytes": index_size_bytes,
            "document_count": source_metrics.get("embedding_document_count", source_metrics.get("record_count", 0)),
            "source_record_count": source_metrics.get("record_count", 0),
            "chunk_count": source_metrics.get("chunk_count", 0),
            "refresh_no_change_cost_ms": refresh_metrics["public"]["elapsed_ms"],
            "refresh_status": refresh_metrics["public"]["status"],
            "reusable_record_count": refresh_metrics["public"].get("reusable_record_count", 0),
        },
        "query_metrics": {
            "case_count": len(rows),
            "gold_case_count": len(gold_rows),
            "baseline_comparison_mode": source_metrics.get("baseline_mode", "correctness"),
            "baseline_correctness_available": bool(source_metrics.get("correctness_comparison_available", True)),
            "median_query_latency_ms": _median(query_latencies),
            "p90_query_latency_ms": _p90(query_latencies),
            "candidate_count_total": sum(int(row.get("candidate_count", 0)) for row in rows),
            "baseline_candidate_count_total": sum(int(row.get("baseline_candidate_count", 0)) for row in rows),
            "embedding_first_pass_hits": sum(1 for row in gold_rows if row["embedding"]["first_pass_hit"]),
            "baseline_first_pass_hits": sum(1 for row in gold_rows if row["baseline"]["first_pass_hit"]),
            "embedding_mean_recall_at_3": _mean(row["embedding"]["recall_at_3"] for row in gold_rows),
            "baseline_mean_recall_at_3": _mean(row["baseline"]["recall_at_3"] for row in gold_rows),
            "protected_regressions": sum(1 for row in rows if row.get("protected_regression") is True),
        },
        "caps_hit": bool(cap_labels),
        "cap_hit_labels": sorted(set(cap_labels)),
        "failure_modes": _failure_modes(source_metrics, cap_labels),
    }


def _onnx_profile_status(args, backend_state: dict[str, Any]) -> dict[str, Any]:
    requested = bool(getattr(args, "enable_onnx_profiling", False))
    if not requested:
        return {"requested": False, "status": "not_requested"}
    if backend_state.get("kind") != FASTEMBED_BACKEND_ID:
        return {
            "requested": True,
            "status": "not_available_backend_not_fastembed",
            "raw_profile_files_committed": False,
        }
    return {
        "requested": True,
        "status": "blocked_fastembed_wrapper_session_options_not_exposed",
        "reason": (
            "Current FastEmbed construction path accepts model/cache/local-files controls only in this harness. "
            "It does not expose an ONNX Runtime InferenceSession or SessionOptions hook where enable_profiling "
            "can be set without relying on private wrapper internals."
        ),
        "next_step": "Add ONNX profiling only through a documented public FastEmbed hook or a separate explicit ONNX Runtime backend.",
        "raw_profile_files_committed": False,
    }


def _root_cause_profile(memory_profile: dict[str, Any]) -> dict[str, Any]:
    if not memory_profile.get("enabled"):
        return {"status": "not_profiled", "hypotheses_ranked": []}

    samples = memory_profile.get("samples", [])

    def sample(phase: str, event: str, counter_key: str | None = None, counter_value: Any = None) -> dict[str, Any] | None:
        for item in samples:
            if item.get("phase") != phase or item.get("event") != event:
                continue
            counters = item.get("counters") or {}
            if counter_key is not None and counters.get(counter_key) != counter_value:
                continue
            return item
        return None

    def rss(item: dict[str, Any] | None) -> int:
        return int((item or {}).get("rss_bytes") or 0)

    def counter(item: dict[str, Any] | None, key: str) -> int:
        value = ((item or {}).get("counters") or {}).get(key) or 0
        return int(value)

    document_load_end = sample("document_load", "end")
    model_constructor_end = sample("fastembed_model_session", "constructor_end")
    embed_start = sample("fastembed_document_batch", "embed_generator_start")
    first_vector = sample("fastembed_document_batch", "vector_checkpoint", "vector_count", 1)
    embed_end = sample("fastembed_document_batch", "embed_generator_end")
    embed_ends = [
        item
        for item in samples
        if item.get("phase") == "fastembed_document_batch" and item.get("event") == "embed_generator_end"
    ]
    vector_retention_end = sample("vector_retention", "end")
    json_index_end = sample("json_serialization", "index_end")
    fastembed_profiled = embed_start is not None and first_vector is not None and embed_end is not None

    peak_rss = int(memory_profile.get("peak_observed_rss_bytes") or 0)
    tracemalloc_peak = int(memory_profile.get("tracemalloc_python_peak_bytes") or 0)
    raw_vector_bytes = sum(counter(item, "raw_float32_bytes_estimate") for item in embed_ends) or counter(embed_end, "raw_float32_bytes_estimate")
    text_bytes = counter(document_load_end, "text_bytes_estimate")
    index_json_bytes = counter(json_index_end, "json_bytes")
    embed_native_jump = max(0, rss(first_vector) - rss(embed_start))

    evidence_summary = {
        "document_count": sum(counter(item, "vector_count") for item in embed_ends) or counter(embed_end, "vector_count"),
        "fastembed_batch_count": len(embed_ends),
        "text_bytes_estimate": text_bytes,
        "raw_float32_vector_bytes_estimate": raw_vector_bytes,
        "index_json_bytes": index_json_bytes,
        "document_load_end_rss_bytes": rss(document_load_end),
        "model_constructor_end_rss_bytes": rss(model_constructor_end),
        "embed_start_rss_bytes": rss(embed_start),
        "first_vector_checkpoint_rss_bytes": rss(first_vector),
        "vector_retention_end_rss_bytes": rss(vector_retention_end),
        "peak_observed_rss_bytes": peak_rss,
        "tracemalloc_python_peak_bytes": tracemalloc_peak,
        "embed_start_to_first_vector_rss_delta_bytes": embed_native_jump,
    }

    if not fastembed_profiled:
        return {
            "status": "profiled",
            "evidence_summary": evidence_summary,
            "hypotheses_ranked": [
                {
                    "rank": 1,
                    "hypothesis": "fastembed_onnx_not_profiled",
                    "confidence": "high",
                    "evidence": "This profile did not run the FastEmbed document-batch path, so it cannot support a FastEmbed/ONNX root-cause claim.",
                    "evidence_counters": {
                        "peak_observed_rss_bytes": peak_rss,
                        "tracemalloc_python_peak_bytes": tracemalloc_peak,
                    },
                    "recommended_follow_up": "Run with backend fastembed and RSS profiling before ranking ONNX/FastEmbed causes.",
                },
                {
                    "rank": 2,
                    "hypothesis": "tracemalloc_only_measurement_is_incomplete",
                    "confidence": "medium",
                    "evidence": "Python tracemalloc still only covers Python heap allocations; RSS samples are required for native/runtime memory behavior.",
                    "evidence_counters": {
                        "peak_observed_rss_bytes": peak_rss,
                        "tracemalloc_python_peak_bytes": tracemalloc_peak,
                    },
                    "recommended_follow_up": "Keep RSS/native phase samples in future embedding gates; do not use tracemalloc alone.",
                },
            ],
        }

    hypotheses = [
        {
            "rank": 1,
            "hypothesis": "fastembed_onnx_batch_native_allocation",
            "confidence": "high",
            "evidence": (
                "RSS is sampled inside bounded FastEmbed document batches. If RSS still rises sharply here while "
                "tracemalloc remains far below RSS, native ONNX/FastEmbed allocation remains the primary risk."
            ),
            "evidence_counters": {
                "embed_start_to_first_vector_rss_delta_bytes": embed_native_jump,
                "tracemalloc_python_peak_bytes": tracemalloc_peak,
                "raw_float32_vector_bytes_estimate": raw_vector_bytes,
            },
            "recommended_follow_up": "Use these per-batch RSS samples in jsh-044c before increasing selected-history scale.",
        },
        {
            "rank": 2,
            "hypothesis": "python_vector_retention_bounded_by_shard_buffer",
            "confidence": "medium",
            "evidence": (
                "The current architecture writes dense vectors into private binary shards and keeps only the active "
                "shard buffer in Python instead of a full document-vector dict."
            ),
            "evidence_counters": {
                "raw_float32_vector_bytes_estimate": raw_vector_bytes,
                "vector_retention_end_rss_bytes": rss(vector_retention_end),
            },
            "recommended_follow_up": "Reviewer should verify the live query path ranks via shard files, not an in-memory vector dict.",
        },
        {
            "rank": 3,
            "hypothesis": "monolithic_json_vector_serialization_replaced",
            "confidence": "medium",
            "evidence": (
                "The prior monolithic JSON vector map is replaced by float32 shard files plus small JSON manifests; "
                "JSON serialization now covers metadata rather than every vector value."
            ),
            "evidence_counters": {
                "index_json_bytes": index_json_bytes,
                "peak_observed_rss_bytes": peak_rss,
            },
            "recommended_follow_up": "Track shard vector bytes, metadata bytes, and manifest bytes separately in jsh-044c.",
        },
        {
            "rank": 4,
            "hypothesis": "document_text_materialization_not_primary_at_profiled_scale",
            "confidence": "high",
            "evidence": (
                "Document loading and text materialization remain in the tens of MiB while the embed phase reaches GiB RSS."
            ),
            "evidence_counters": {
                "text_bytes_estimate": text_bytes,
                "document_load_end_rss_bytes": rss(document_load_end),
            },
            "recommended_follow_up": "Still stream records in jsh-044b, but treat text loading as a lower-ranked cause.",
        },
        {
            "rank": 5,
            "hypothesis": "tracemalloc_only_measurement_is_misleading",
            "confidence": "high",
            "evidence": (
                "Python tracemalloc observes only Python heap allocations and misses the native RSS behavior visible here."
            ),
            "evidence_counters": {
                "peak_observed_rss_bytes": peak_rss,
                "tracemalloc_python_peak_bytes": tracemalloc_peak,
            },
            "recommended_follow_up": "Keep RSS/native phase samples in future embedding gates; do not use tracemalloc alone.",
        },
    ]
    return {
        "status": "profiled",
        "evidence_summary": evidence_summary,
        "hypotheses_ranked": hypotheses,
    }


def _embedding_recall_gate(rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> dict[str, Any]:
    query_metrics = aggregate["query_metrics"]
    if not query_metrics.get("baseline_correctness_available", True):
        return _gate("inconclusive", ["baseline_comparison_skipped_resource_measurement_only"], query_metrics)
    if aggregate.get("caps_hit"):
        return _gate("inconclusive", ["cap_hits_present"], query_metrics)
    if not query_metrics["gold_case_count"]:
        return _gate("not_evaluated", ["no_gold_cases_available"], query_metrics)
    if any(row.get("query_source") == "fixture_case" for row in rows):
        return _gate(
            "inconclusive",
            ["synthetic_fixture_only", "legitimate_private_evidence_not_evaluated"],
            query_metrics,
        )
    return _gate("inconclusive", ["verified_case_status_unavailable"], query_metrics)


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


def _load_probes(args) -> tuple[list[ExperimentProbe], list[str]]:
    if args.queries:
        return (
            [
                ExperimentProbe(
                    query_text=query,
                    case_id=f"query_{index + 1:03d}",
                    case_family="explicit_query",
                    query_source="explicit_query",
                )
                for index, query in enumerate(args.queries[: args.case_limit])
            ],
            [],
        )
    fixture_path = Path(args.fixture).expanduser()
    try:
        payload = load_embedding_fixture(fixture_path)
    except OSError:
        return [], ["fixture could not be read"]
    except json.JSONDecodeError:
        return [], ["fixture is not valid JSON"]
    errors = validate_embedding_fixture(payload)
    if errors:
        return [], [f"fixture validation failed: {error}" for error in errors[:5]]
    probes = []
    for case in payload.get("cases", [])[: args.case_limit]:
        probes.append(
            ExperimentProbe(
                query_text=str(case["query"]),
                case_id=str(case["case_id"]),
                case_family=_gate_family(case),
                query_source="fixture_case",
                gold_doc_id=str(case.get("gold_doc_id")) if args.experiment_mode == "smoke_synthetic" else None,
            )
        )
    return probes, []


def _gate_family(case: dict[str, Any]) -> str:
    case_id = str(case.get("case_id") or "")
    scenario = str(case.get("scenario") or "")
    if str(case.get("case_family")) == "valuable":
        return "vague_paraphrase_theme"
    if "exact quote" in scenario:
        return "exact_quote"
    if "file path" in scenario:
        return "path"
    if "task slug" in scenario:
        return "task_slug"
    if "command" in scenario:
        return "command"
    if "date" in scenario:
        return "date_window"
    if "id" in scenario.lower() or "id_like" in case_id:
        return "id_like_token"
    return "protected_exact_like"


def _base_report(args, run_id: str, limits: Limits, started: float) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": "pending",
        "experiment_mode": args.experiment_mode,
        "data_source": "committed_sanitized_synthetic_fixture"
        if args.experiment_mode == "smoke_synthetic"
        else "live_local_roots",
        "limits": {
            **limits.to_dict(),
            "max_records": args.max_records,
            "max_index_disk": args.max_index_disk,
            "fastembed_batch_size": args.fastembed_batch_size,
            "shard_size": args.shard_size,
            "chunk_size_chars": args.chunk_size_chars,
            "chunk_overlap_chars": args.chunk_overlap_chars,
            "baseline_mode": getattr(args, "baseline_mode", "correctness"),
        },
        "scope": {
            "selected_roots": [],
            "rejected_roots": [],
            "date_window_label": _date_window_label(args),
            "full_history_approved": bool(args.approve_full_history),
        },
        "source_metrics": {},
        "created_elapsed_ms": _elapsed_ms(started),
    }


def _scope_payload(plan: SourcePlan, args) -> dict[str, Any]:
    return {
        "selected_roots": [_safe_root_payload(root) for root in sanitized_selected_roots(plan)],
        "rejected_roots": [_safe_rejected_root_payload(root) for root in sanitized_rejected_roots(plan)],
        "date_window_label": _date_window_label(args),
        "full_history_approved": bool(args.approve_full_history),
        "not_searched_scope": [
            "Only selected approved roots were considered.",
            "Raw local paths, filenames, session IDs, snippets, query text, and vectors are omitted from committed-safe output.",
        ],
    }


def _safe_root_payload(root: dict[str, Any]) -> dict[str, Any]:
    return {
        "root_label": root.get("root_label"),
        "kind": root.get("kind"),
        "reason": root.get("reason"),
        "recursive": root.get("recursive"),
        "known": root.get("known"),
        "exists": root.get("exists"),
        "scope_label": root.get("scope_label"),
        "privacy_label": root.get("privacy_label"),
    }


def _safe_rejected_root_payload(root: dict[str, Any]) -> dict[str, Any]:
    return {
        "root_label": root.get("root_label"),
        "reason": root.get("reason"),
        "scope_label": root.get("scope_label"),
    }


def _finish_without_scan(report: dict[str, Any], status: str, warnings: list[str], started: float) -> dict[str, Any]:
    safe = _safe_warnings(warnings)
    report["status"] = status
    report["aggregate"] = {
        "overall_status": status,
        "total_elapsed_ms": _elapsed_ms(started),
        "caps_hit": False,
        "cap_hit_labels": [],
        "failure_modes": safe,
    }
    report["backend"] = {
        "requested": "not_started",
        "selected": "not_started",
        "fallback_used": False,
    }
    report["rows"] = []
    report["warnings"] = safe
    report["embedding_recall_gate"] = {"status": "not_evaluated", "reasons": [f"experiment_status_{status}"]}
    report["privacy_contract"] = _privacy_contract()
    report["production_status"] = "experimental_unpromoted_not_default"
    report["default_live_search_changed"] = False
    return _add_metric_sections(report)


def _add_metric_sections(report: dict[str, Any]) -> dict[str, Any]:
    return add_metric_contract(report, embedding_experiment_metrics(report))


def _private_output_dir(raw_output_dir: str | None, now: datetime) -> Path:
    if raw_output_dir:
        return Path(raw_output_dir).expanduser().resolve()
    return (Path(".private") / "jsh-035b" / f"run-{now.strftime('%Y%m%d-%H%M%S')}").resolve()


def _is_ignored_private_path(path: Path) -> bool:
    return ".private" in path.parts


def _date_window_label(args) -> str:
    if args.experiment_mode != "date_window_subset":
        return "not_applicable"
    if args.window_start or args.window_end:
        return "date_window_supplied"
    return "date_window_missing"


def _parse_window(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _in_window(value: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    if value is None:
        return False
    if start and value < start:
        return False
    if end and value > end:
        return False
    return True


def _parse_fixture_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _gold_rank(hits: list[RankedHit], gold_doc_id: str | None) -> int | None:
    if not gold_doc_id:
        return None
    for hit in hits:
        if hit.doc_id == gold_doc_id:
            return hit.rank
    return None


def _first_pass_delta(embedding_rank: int | None, baseline_rank: int | None) -> int | None:
    if embedding_rank is None or baseline_rank is None:
        return None
    return (1 if embedding_rank == 1 else 0) - (1 if baseline_rank == 1 else 0)


def _protected_regression(case_family: str, embedding_rank: int | None, baseline_rank: int | None) -> bool | None:
    if case_family not in PROTECTED_FAMILIES:
        return False
    if baseline_rank is None:
        return False
    if embedding_rank is None:
        return True
    return embedding_rank > baseline_rank


def _content_fingerprint(documents: list[ExperimentDocument]) -> str:
    digest = hashlib.sha256()
    for doc in documents:
        _update_content_fingerprint(digest, doc)
    return f"sha256:{digest.hexdigest()}"


def _update_content_fingerprint(digest, doc: ExperimentDocument) -> None:
    digest.update(doc.doc_id.encode("utf-8"))
    digest.update(b"\0")
    digest.update(doc.text.encode("utf-8", errors="replace"))
    digest.update(b"\0")


def _run_id(now: datetime) -> str:
    seed = f"{now.isoformat()}:{time.perf_counter_ns()}"
    return f"embedding-run-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:12]}"


def _privacy_contract() -> dict[str, Any]:
    return {
        "raw_private_text_committed": False,
        "private_vectors_committed": False,
        "private_indexes_committed": False,
        "raw_query_text_committed": False,
        "raw_transcript_paths_committed": False,
        "exact_artifact_paths_may_be_committed": True,
        "private_artifact_storage": "ignored_dot_private_only",
        "remote_upload": False,
        "default_search_promotion": False,
    }


def _failure_modes(source_metrics: dict[str, Any], cap_labels: list[str]) -> list[str]:
    failures = set(source_metrics.get("warnings", []))
    failures.update(f"cap_hit_{label}" for label in cap_labels)
    return sorted(failures)


def _safe_warnings(warnings: Iterable[str]) -> list[str]:
    safe: set[str] = set()
    for warning in warnings:
        lowered = str(warning).lower()
        if "max elapsed cap" in lowered:
            safe.add("stopped_at_max_elapsed_cap")
        elif "max file cap" in lowered:
            safe.add("stopped_at_max_file_cap")
        elif "max byte cap" in lowered:
            safe.add("stopped_at_max_byte_cap")
        elif "max records cap" in lowered:
            safe.add("stopped_at_max_records_cap")
        elif "arbitrary root override requires" in lowered:
            safe.add("root_rejected_missing_arbitrary_root_hard_caps")
        elif "dangerous broad root" in lowered:
            safe.add("root_rejected_dangerous_broad_root")
        elif "repo root rejected" in lowered:
            safe.add("root_rejected_repo_root")
        elif "cache/model directory rejected" in lowered:
            safe.add("root_rejected_cache_or_model_root")
        elif "not a known codex/claude transcript root" in lowered:
            safe.add("root_rejected_unknown_without_override")
        elif "arbitrary root enabled" in lowered:
            safe.add("arbitrary_root_enabled_with_hard_caps")
        elif "no explicit approved roots" in lowered:
            safe.add("no_explicit_approved_roots")
        elif "missing transcript root" in lowered:
            safe.add("missing_root")
        elif "could not" in lowered:
            safe.add("read_or_enumeration_error")
        else:
            safe.add(_opaque_warning(str(warning)))
    return sorted(safe)


def _sanitize_exception(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}_{hashlib.sha256(str(exc).encode('utf-8', errors='replace')).hexdigest()[:10]}"


def _opaque_warning(warning: str) -> str:
    return f"warning_{hashlib.sha256(warning.encode('utf-8', errors='replace')).hexdigest()[:12]}"


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _current_rss_bytes() -> int:
    status_path = Path("/proc/self/status")
    try:
        for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) >= 2:
                    return int(parts[1]) * 1024
    except (OSError, ValueError):
        pass
    return 0


def _max_rss_bytes() -> int:
    max_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return max_rss * 1024


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return round((ordered[middle - 1] + ordered[middle]) / 2, 3)


def _p90(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.9) - 1)
    return float(ordered[index])


def _mean(values: Iterable[float | None]) -> float | None:
    items = [value for value in values if value is not None]
    if not items:
        return None
    return round(sum(items) / len(items), 3)
