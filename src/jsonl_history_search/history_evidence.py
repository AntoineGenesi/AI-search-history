"""Ordinary history evidence: retrieval, bounded raw reads, and honest coverage.

This operation owns no reports, continuation state, CLI, or final-answer gates.
Ranking locates evidence; raw records establish what was said or invoked.
Architecture: reports/jsh-1554b-architecture-addendum.md.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from .adapters import AdapterReadError, RawLineScanStats, iter_records_for_file
from .agent_contract import build_candidate_rows
from .operation_budget import BudgetExceeded
from .retrieval_modes import run_retrieval_mode
from .source_expansion import resolve_source_path
from .source_refs import evidence_ref, ref_target, evidence_identity
from .sources import source_kind_for_path, cap_labels_from_warnings, path_is_inside
from .tool_events import detect_events_for_record

MAX_PRESENTATION_BYTES = 64 * 1024


def _masked(value, *, record, mask_context):
    if mask_context is None:
        return value
    if isinstance(value, str):
        return mask_context.mask_text(value, source_path=record.path, source_index=record.line)
    if isinstance(value, dict):
        return {key: _masked(item, record=record, mask_context=mask_context) for key, item in value.items()}
    if isinstance(value, list):
        return [_masked(item, record=record, mask_context=mask_context) for item in value]
    return value


def _in_scope(path, plan):
    return any(path_is_inside(Path(path), root.path if root.path.is_dir() else root.path.parent)
               for root in plan.roots)


def _visible(record, intent):
    if intent.role_filter_explicit and record.role != intent.role:
        return False
    if intent.date_window_filter:
        if record.timestamp is None:
            return False
        if intent.date_window_start and record.timestamp < intent.date_window_start:
            return False
        if intent.date_window_end and record.timestamp > intent.date_window_end:
            return False
    return True


def retrieve_history_evidence(*, request, source_plan, intent, plan, default_tz,
                              retrieval_options, budget, mask_context=None):
    result = run_retrieval_mode(plan=plan, intent=intent, default_tz=default_tz,
                                cwd=source_plan["cwd"], mask_context=mask_context,
                                budget=budget, **retrieval_options) if budget.available("retrieval") else {
                                    "groups": [], "warnings": ["Stopped retrieval at operation max elapsed cap."]}
    candidates = build_candidate_rows(result)
    admitted_source_files = result.get("admitted_source_files", result.get("planned_files_count", 0))
    # Capture retrieval reads before candidate-dependent readback. Repeated lanes
    # and fingerprint passes count once here, unlike logical extent metrics.
    source_read_attempts = len(budget.opened_sources)
    by_source = OrderedDict()
    for candidate in candidates:
        by_source.setdefault(candidate.path, []).append(candidate)
    windows, refs, events, gaps, errors = [], [], [], [], []
    seen = set()
    presentation_bytes = 0
    omitted_records = []
    for path, source_candidates in by_source.items():
        source_path = resolve_source_path(path, plan=plan, cwd=Path(source_plan["cwd"]))
        if source_path is None:
            gaps.append({"status": "source_not_found_or_out_of_scope", "source": path})
            continue
        records = []
        stats = RawLineScanStats()
        source_complete = True
        reader = iter_records_for_file(source_path, source_kind_for_path(source_path, plan),
                                        default_tz, raw_line_stats=stats, budget=budget,
                                        budget_phase="readback")
        try:
            for record in reader:
                records.append(record)
        except BudgetExceeded as exc:
            source_complete = False
            gaps.append({"status": "read_budget_exhausted", "source": path, "reason": str(exc)})
        except (AdapterReadError, OSError) as exc:
            source_complete = False
            errors.append({"source": path, "reason": str(exc)})
        finally:
            reader.close()
        errors.extend(stats.partial_failures or [])
        selected_positions = set()
        for candidate in source_candidates:
            native = candidate.source_ref.get("native_ref")
            positions = [index for index, record in enumerate(records)
                         if native == record.source_ref or native in (record.source_refs or [])] if native else [
                             index for index, record in enumerate(records) if record.line == candidate.line]
            if len(positions) != 1:
                gaps.append({"status": "ambiguous_native_ref" if len(positions) > 1 else "candidate_window_not_opened",
                             "target": ref_target(candidate.source_ref), "matches": len(positions)})
                continue
            position = positions[0]
            selected_positions.update(range(max(0, position - 3), min(len(records), position + 4)))
        opened = []
        for position in sorted(selected_positions):
            record = records[position]
            if not _visible(record, intent) or not record.text.strip():
                continue
            identity = evidence_identity(record)
            if identity in seen:
                continue
            seen.add(identity)
            ref = evidence_ref(record)
            passage = _masked(record.text, record=record, mask_context=mask_context)
            available = max(0, MAX_PRESENTATION_BYTES - presentation_bytes)
            encoded = passage.encode("utf-8")
            if len(encoded) > available:
                omitted_records.append({"target": ref_target(ref), "omitted_text_bytes": len(encoded) - available})
                passage = encoded[:available].decode("utf-8", errors="ignore")
            presentation_bytes += len(passage.encode("utf-8"))
            opened.append({"text": passage, "role": record.role,
                           "timestamp": record.timestamp.isoformat() if record.timestamp else None,
                           "timestamp_quality": record.timestamp_quality, "source_ref": ref,
                           "target": ref_target(ref), "line": record.line})
            refs.append({"target": ref_target(ref), "source_ref": ref,
                         "kind": "raw_history_record", "target_source_role": record.role})
            for event in detect_events_for_record(record, mask_context=mask_context):
                serialized = _masked(event.to_dict(), record=record, mask_context=mask_context)
                # Raw action arguments are already represented in bounded passages.
                # Retain classification/ref facts without duplicating unbounded text.
                events.append({key: value for key, value in serialized.items()
                               if key not in {"arguments", "payload_excerpt", "command"}})
        if opened:
            windows.append({"source": path, "records": opened,
                            "read_complete": source_complete and not stats.partial_failures,
                            "window_policy": "ranked anchors with up to three adjacent records on either side"})
    warnings = list(result.get("warnings", []))
    cap_reasons = cap_labels_from_warnings(warnings)
    cap_reasons.extend(budget.exhausted_reasons)
    diagnostics = result.get("source_diagnostics", {})
    errors.extend(item for item in diagnostics.get("partial_failures", [])
                  if item.get("path") and _in_scope(item["path"], plan))
    errors.extend(item for item in result.get("operation_source_errors", [])
                  if item.get("path") and _in_scope(item["path"], plan))
    errors = list({(item.get("path"), item.get("line"), item.get("provider"), item.get("reason")): item
                   for item in errors}.values())
    # A native/file reader's missing or malformed records cannot become absence.
    complete = not (cap_reasons or errors or gaps)
    inventory = [{**root.to_dict(), "selected_for_direct_answer": True,
                  "status": "selected"} for root in plan.roots]
    if windows:
        status = "evidence_found"
    elif not admitted_source_files and not candidates and not cap_reasons and not errors:
        status = "empty_scope"
        complete = False
    elif not complete:
        status = "partial_coverage"
    else:
        status = "not_found"
    passages = [f"{record['target']} [{record['role']}]: {record['text']}"
                for window in windows for record in window["records"]]
    return {"ask_answer": {
        "query": request["query"], "selected_route": request["route"],
        "answer_status": status,
        "answer": "Retrieved source evidence. Use these passages to answer the question; ranking is not causal proof."
                  if windows else "No opened evidence in the reported scope; inspect coverage and gaps before claiming absence.",
        "evidence_windows": windows, "refs_used": refs, "action_evidence": events,
        "proof_summary": passages,
        "candidate_diagnostics": [{"rank": c.rank, "source_ref": c.source_ref,
                                   "snippet": c.snippet, "snippet_is_window_proof": False,
                                   "score": c.score, "score_kind": retrieval_options["retrieval_mode"]}
                                  for c in candidates],
        "source_contract": {"built_from": "direct_history_evidence",
            "legacy_packet_or_execute_continuation_used": False,
            "coverage": {"complete": complete, "status": "complete_within_selected_scope" if complete else "partial",
                         "searched_files": source_read_attempts,
                         "admitted_source_files": admitted_source_files,
                         "source_read_attempts": source_read_attempts,
                         "candidate_source_files": len(by_source),
                         "source_count_basis": "admitted files, distinct retrieval source-read attempts, and candidate files are separate; read attempts are not successful validation",
                         "records_considered": result.get("records_considered", result.get("records_read", 0)),
                         "cap_reasons": sorted(set(cap_reasons))},
            "source_inventory": inventory, "source_errors": errors,
            "recoverable_gaps": gaps, "warnings": warnings,
            "output_omissions": {"omitted_window_count": len(gaps),
                                 "presentation_truncated": bool(omitted_records),
                                 "presentation_text_byte_limit": MAX_PRESENTATION_BYTES,
                                 "omitted_records": omitted_records},
            "operation_budget": budget.to_dict(), "limits": plan.limits.to_dict(),
            "retrieval_diagnostics": result.get("retrieval_diagnostics", {}),
            "historical_instructions_are_evidence": True,
            "scope": source_plan["sources"],
            "masking": mask_context.stats.to_dict() if mask_context is not None else None,
            "claim_boundary": "bounded source evidence; no automatic causal conclusion or exhaustive history claim"}}}
