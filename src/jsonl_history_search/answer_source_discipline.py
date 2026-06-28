from __future__ import annotations

from typing import Any


def build_answer_source_discipline(
    *,
    candidates: list[dict[str, Any]],
    source_locator_handoff: dict[str, Any],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
    no_evidence: dict[str, Any],
    next_reads: list[dict[str, Any]],
    decision_context: dict[str, Any] | None = None,
    answer_readiness: dict[str, Any] | None = None,
    expected_handles: dict[str, Any] | None = None,
    follow_next_reads: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final_contract = (
        source_locator_handoff.get("final_evidence_contract")
        if isinstance(source_locator_handoff.get("final_evidence_contract"), dict)
        else {}
    )
    ledger = [item for item in final_contract.get("ledger") or [] if isinstance(item, dict)]
    candidate_handles = _candidate_handles(candidates, ledger)
    opened_handles = _opened_or_packaged_handles(ledger)
    recovery_handles = _recovery_handles(ledger, source_locator_handoff, next_reads)
    supporting_handles = _supporting_handles(ledger)
    unsupported_extra_handles = _unsupported_extra_handles(candidate_handles, ledger)
    missing_expected_handles = _missing_expected_handles(expected_handles)
    handle_ledger = {
        "schema_version": 1,
        "candidate_handles": candidate_handles,
        "opened_or_packaged_handles": opened_handles,
        "recovery_handles": recovery_handles,
        "supporting_handles": supporting_handles,
        "unsupported_extra_handles": unsupported_extra_handles,
        "missing_expected_handles": missing_expected_handles,
        "counts": {
            "candidate": len(candidate_handles),
            "opened_or_packaged": len(opened_handles),
            "recovery": len(recovery_handles),
            "supporting": len(supporting_handles),
            "unsupported_extra": len(unsupported_extra_handles),
            "missing_expected": len(missing_expected_handles.get("handles") or []),
        },
        "support_boundary": (
            "candidate handles are retrieval leads only; final support requires opened, packaged, "
            "or structured proof surfaces for the relevant facet"
        ),
        "privacy": _privacy_flags(),
    }
    proof_action_coverage = _proof_action_coverage(
        tool_proof=tool_proof,
        joins=joins,
        decision_context=decision_context or {},
    )
    recovery_read_accountability = _recovery_read_accountability(
        source_locator_handoff=source_locator_handoff,
        next_reads=next_reads,
        follow_next_reads=follow_next_reads,
    )
    answer_gate = _answer_gate(
        handle_ledger=handle_ledger,
        proof_action_coverage=proof_action_coverage,
        recovery_read_accountability=recovery_read_accountability,
        no_evidence=no_evidence,
        answer_readiness=answer_readiness or {},
    )
    eval_facet_hooks = _eval_facet_hooks(
        handle_ledger=handle_ledger,
        proof_action_coverage=proof_action_coverage,
        answer_gate=answer_gate,
        recovery_read_accountability=recovery_read_accountability,
        no_evidence=no_evidence,
    )
    return {
        "schema_version": 1,
        "handle_ledger": handle_ledger,
        "proof_action_coverage": proof_action_coverage,
        "answer_gate": answer_gate,
        "recovery_read_accountability": recovery_read_accountability,
        "eval_facet_hooks": eval_facet_hooks,
        "privacy_boundaries": _privacy_flags(),
    }


def update_recovery_read_accountability(
    discipline: dict[str, Any],
    *,
    follow_next_reads: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(discipline, dict):
        return {}
    updated = dict(discipline)
    current = (
        updated.get("recovery_read_accountability")
        if isinstance(updated.get("recovery_read_accountability"), dict)
        else {}
    )
    next_reads = [{"target_handle": item.get("target_handle")} for item in current.get("top_recovery_targets") or []]
    updated["recovery_read_accountability"] = _recovery_read_accountability(
        source_locator_handoff={},
        next_reads=next_reads,
        follow_next_reads=follow_next_reads,
        previous=current,
    )
    updated["answer_gate"] = _answer_gate(
        handle_ledger=updated.get("handle_ledger") if isinstance(updated.get("handle_ledger"), dict) else {},
        proof_action_coverage=(
            updated.get("proof_action_coverage")
            if isinstance(updated.get("proof_action_coverage"), dict)
            else {}
        ),
        recovery_read_accountability=updated["recovery_read_accountability"],
        no_evidence={},
        answer_readiness={},
        previous=updated.get("answer_gate") if isinstance(updated.get("answer_gate"), dict) else {},
    )
    updated["eval_facet_hooks"] = _eval_facet_hooks(
        handle_ledger=updated.get("handle_ledger") if isinstance(updated.get("handle_ledger"), dict) else {},
        proof_action_coverage=(
            updated.get("proof_action_coverage")
            if isinstance(updated.get("proof_action_coverage"), dict)
            else {}
        ),
        answer_gate=updated["answer_gate"],
        recovery_read_accountability=updated["recovery_read_accountability"],
        no_evidence={},
    )
    return updated


def answer_source_discipline_summary(discipline: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(discipline, dict):
        return {}
    ledger = discipline.get("handle_ledger") if isinstance(discipline.get("handle_ledger"), dict) else {}
    proof = discipline.get("proof_action_coverage") if isinstance(discipline.get("proof_action_coverage"), dict) else {}
    gate = discipline.get("answer_gate") if isinstance(discipline.get("answer_gate"), dict) else {}
    recovery = (
        discipline.get("recovery_read_accountability")
        if isinstance(discipline.get("recovery_read_accountability"), dict)
        else {}
    )
    return {
        "schema_version": discipline.get("schema_version", 1),
        "candidate_only_citation_allowed": gate.get("candidate_only_citation_allowed", False),
        "unsupported_extra_handle_citation_allowed": gate.get(
            "unsupported_extra_handle_citation_allowed",
            False,
        ),
        "decision": gate.get("decision"),
        "blocked_facets": list(gate.get("blocked_facets") or [])[:8],
        "handle_counts": ledger.get("counts") or {},
        "missing_proof_buckets": list(proof.get("missing_required_buckets") or [])[:8],
        "recovery_status": recovery.get("status"),
        "follow_next_reads_loaded": recovery.get("follow_next_reads_loaded", False),
        "eval_facet_hooks": discipline.get("eval_facet_hooks") or {},
    }


def _candidate_handles(candidates: list[dict[str, Any]], ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ledger_by_candidate = {
        str(item.get("candidate_id")): item
        for item in ledger
        if item.get("candidate_id") not in (None, "")
    }
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates[:12], 1):
        candidate_id = str(candidate.get("candidate_id") or f"candidate-{index:03d}")
        ledger_item = ledger_by_candidate.get(candidate_id) or {}
        rows.append(
            _compact_row(
                {
                    "handle_id": f"candidate:{candidate_id}",
                    "candidate_id": candidate_id,
                    "ledger_id": ledger_item.get("ledger_id"),
                    "support_status": "candidate_only",
                    "final_support_allowed": False,
                    "reason": "retrieval_candidate_not_final_claim_support",
                    "anchor_families": _anchor_families(candidate, ledger_item),
                }
            )
        )
    return rows


def _opened_or_packaged_handles(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in ledger:
        membership = item.get("membership") if isinstance(item.get("membership"), dict) else {}
        if not (membership.get("source_windows") or membership.get("evidence_packet_children")):
            continue
        rows.append(
            _compact_row(
                {
                    "handle_id": _ledger_handle_id(item),
                    "ledger_id": item.get("ledger_id"),
                    "candidate_id": item.get("candidate_id") or None,
                    "support_status": "opened_or_packaged",
                    "answerability_tier": item.get("answerability_tier"),
                    "final_surface_status": item.get("final_surface_status"),
                    "final_support_allowed": True,
                    "support_boundary": "supports searched-scope claims only for facets represented by this surface",
                    "anchor_families": _anchor_families({}, item),
                }
            )
        )
    return rows[:12]


def _supporting_handles(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in ledger:
        if item.get("final_surface_status") != "present":
            continue
        rows.append(
            _compact_row(
                {
                    "handle_id": _ledger_handle_id(item),
                    "ledger_id": item.get("ledger_id"),
                    "candidate_id": item.get("candidate_id") or None,
                    "support_status": "claim_supporting_for_available_surface",
                    "final_support_allowed": True,
                    "required_facet_boundary": "proof/action facets still require structured proof coverage",
                    "anchor_families": _anchor_families({}, item),
                }
            )
        )
    return rows[:12]


def _recovery_handles(
    ledger: list[dict[str, Any]],
    source_locator_handoff: dict[str, Any],
    next_reads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for item in ledger:
        membership = item.get("membership") if isinstance(item.get("membership"), dict) else {}
        if not (membership.get("omitted_window_recovery") or item.get("final_surface_status") == "deferred_with_next_read"):
            continue
        recovery_handle = item.get("recovery_handle") if isinstance(item.get("recovery_handle"), dict) else {}
        rows.append(
            _compact_row(
                {
                    "handle_id": _ledger_handle_id(item),
                    "ledger_id": item.get("ledger_id"),
                    "candidate_id": item.get("candidate_id") or None,
                    "recovery_id": recovery_handle.get("recovery_id"),
                    "support_status": "recovery_or_read_more_required",
                    "final_support_allowed": False,
                    "final_surface_status": item.get("final_surface_status"),
                    "omission_reason": item.get("omission_reason"),
                    "anchor_families": _anchor_families({}, item),
                }
            )
        )
    seen = {row.get("handle_id") for row in rows}
    for index, read in enumerate(next_reads[:12], 1):
        if not isinstance(read, dict) or read.get("omitted_window_recovery") is None:
            continue
        handle_id = f"next_read:{index:03d}"
        if handle_id in seen:
            continue
        omitted = read.get("omitted_window_recovery") if isinstance(read.get("omitted_window_recovery"), dict) else {}
        rows.append(
            _compact_row(
                {
                    "handle_id": handle_id,
                    "recovery_id": omitted.get("recovery_id"),
                    "support_status": "recovery_or_read_more_required",
                    "final_support_allowed": False,
                    "omission_reason": omitted.get("omission_reason"),
                    "anchor_families": ["recovery_ref"],
                }
            )
        )
    omitted = source_locator_handoff.get("omitted_window_recovery")
    if not rows and isinstance(omitted, dict):
        for index, item in enumerate((omitted.get("items") or [])[:12], 1):
            rows.append(
                _compact_row(
                    {
                        "handle_id": f"recovery:{index:03d}",
                        "candidate_id": item.get("candidate_id"),
                        "recovery_id": item.get("recovery_id"),
                        "support_status": "recovery_or_read_more_required",
                        "final_support_allowed": False,
                        "omission_reason": item.get("omission_reason"),
                        "anchor_families": ["recovery_ref"],
                    }
                )
            )
    return rows[:12]


def _unsupported_extra_handles(
    candidate_handles: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    support_by_candidate = {
        str(item.get("candidate_id")): item.get("final_surface_status")
        for item in ledger
        if item.get("candidate_id") not in (None, "")
    }
    rows = []
    for row in candidate_handles:
        candidate_id = str(row.get("candidate_id") or "")
        status = support_by_candidate.get(candidate_id)
        if status == "present":
            continue
        rows.append(
            {
                "handle_id": row.get("handle_id"),
                "candidate_id": row.get("candidate_id"),
                "ledger_id": row.get("ledger_id"),
                "support_status": "unsupported_extra_candidate_or_recovery_lead",
                "final_support_allowed": False,
                "reason": "not_present_in_final_support_surface",
            }
        )
    return rows[:12]


def _missing_expected_handles(expected_handles: dict[str, Any] | None) -> dict[str, Any]:
    if not expected_handles:
        return {
            "status": "unavailable_without_eval_expectations",
            "handles": [],
            "basis": "generic live search does not know private expected handles",
        }
    handles = [
        {"expected_handle_id": str(value)[:120], "status": "missing"}
        for value in expected_handles.get("missing") or []
    ]
    return {
        "status": "available_from_eval_context",
        "handles": handles[:12],
        "basis": "caller_supplied_eval_expectations",
    }


def _proof_action_coverage(
    *,
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
    decision_context: dict[str, Any],
) -> dict[str, Any]:
    routes = set(decision_context.get("selected_routes") or [])
    event_count = int(tool_proof.get("event_count") or 0)
    events = [event for event in tool_proof.get("events") or [] if isinstance(event, dict)]
    event_families = {
        str(event.get("event_family") or event.get("event_type") or event.get("action") or "").lower()
        for event in events
    }
    targets_count = sum(len(event.get("targets") or []) for event in events)
    review_count = len(joins.get("review_evidence") or [])
    artifact_count = len(joins.get("artifact_checks") or [])
    buckets = {
        "command": _proof_bucket(
            required=("tool_action_proof" in routes),
            present=event_count > 0,
            source_surface="semantic_debug.tool_action_proof.events",
            supporting_event_ids=[event.get("event_id") for event in events[:8] if event.get("event_id")],
        ),
        "tool_event": _proof_bucket(
            required=("tool_action_proof" in routes),
            present=event_count > 0,
            source_surface="semantic_debug.tool_action_proof.events",
            supporting_event_ids=[event.get("event_id") for event in events[:8] if event.get("event_id")],
        ),
        "patch": _proof_bucket(
            required=("tool_action_proof" in routes),
            present=bool(targets_count or {"edit", "patch", "write"} & event_families),
            source_surface="semantic_debug.tool_action_proof.events.targets",
            supporting_event_ids=[event.get("event_id") for event in events[:8] if event.get("targets")],
        ),
        "file": _proof_bucket(
            required=("git_history_reconstruction" in routes),
            present=artifact_count > 0,
            source_surface="semantic_debug.task_review_commit_joins.artifact_checks",
            supporting_event_ids=[],
        ),
        "review": _proof_bucket(
            required=("task_review_join" in routes),
            present=review_count > 0 or joins.get("status") == "joined",
            source_surface="semantic_debug.task_review_commit_joins.review_evidence",
            supporting_event_ids=[],
        ),
    }
    missing_required = [
        name
        for name, bucket in buckets.items()
        if bucket.get("required") and not bucket.get("present")
    ]
    return {
        "schema_version": 1,
        "buckets": buckets,
        "missing_required_buckets": missing_required,
        "coverage_boundary": (
            "structured proof buckets cover action/source facets only; transcript candidates are not "
            "command, patch, file, review, or tool-event proof by themselves"
        ),
        "privacy": _privacy_flags(),
    }


def _proof_bucket(
    *,
    required: bool,
    present: bool,
    source_surface: str,
    supporting_event_ids: list[Any],
) -> dict[str, Any]:
    return {
        "required": bool(required),
        "present": bool(present),
        "source_surface": source_surface,
        "supporting_event_ids": [str(value) for value in supporting_event_ids if value not in (None, "")][:8],
        "missing_reason": None if present else ("required_proof_surface_not_present" if required else "not_required_or_not_detected"),
    }


def _answer_gate(
    *,
    handle_ledger: dict[str, Any],
    proof_action_coverage: dict[str, Any],
    recovery_read_accountability: dict[str, Any],
    no_evidence: dict[str, Any],
    answer_readiness: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    missing_proof = list(proof_action_coverage.get("missing_required_buckets") or [])
    blocked_facets = []
    if missing_proof:
        blocked_facets.append("proof_command_source_action_missing")
    if recovery_read_accountability.get("status") in {"recovery_targets_unopened", "follow_next_reads_not_loaded"}:
        blocked_facets.append("recovery_not_opened")
    if (handle_ledger.get("counts") or {}).get("unsupported_extra", 0):
        blocked_facets.append("unsupported_extra_handle_risk")
    if no_evidence.get("needed"):
        decision = "state_scoped_no_evidence"
    elif missing_proof:
        decision = "answer_with_missing_proof_boundary"
    elif recovery_read_accountability.get("status") in {"recovery_targets_unopened", "follow_next_reads_not_loaded"}:
        decision = "inspect_recovery_reads"
    elif answer_readiness.get("decision"):
        decision = answer_readiness.get("decision")
    else:
        decision = (previous or {}).get("decision") or "use_source_boundaries"
    if previous:
        blocked_facets = sorted(set(blocked_facets + list(previous.get("blocked_facets") or [])))
    return {
        "schema_version": 1,
        "candidate_only_citation_allowed": False,
        "unsupported_extra_handle_citation_allowed": False,
        "missing_proof_must_be_stated": True,
        "decision": decision,
        "blocked_facets": blocked_facets[:12],
        "required_non_claims": [
            "candidate-only handles are not final support",
            "unsupported extras must be named as unsupported or omitted from final support",
            "missing proof/action facets must be stated as missing",
        ],
        "readiness_decision_before_discipline": answer_readiness.get("decision"),
        "readiness_status_before_discipline": answer_readiness.get("status"),
    }


def _recovery_read_accountability(
    *,
    source_locator_handoff: dict[str, Any],
    next_reads: list[dict[str, Any]],
    follow_next_reads: dict[str, Any] | None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    omitted = source_locator_handoff.get("omitted_window_recovery")
    omitted = omitted if isinstance(omitted, dict) else {}
    recovery_count = int(omitted.get("recovery_count") or (previous or {}).get("recovery_target_count") or 0)
    promoted_count = int(omitted.get("promoted_next_read_count") or (previous or {}).get("promoted_next_read_count") or 0)
    recovery_reads = [
        read
        for read in next_reads
        if isinstance(read, dict)
        and (
            read.get("omitted_window_recovery")
            or str(read.get("kind") or "").startswith("recovery")
            or read.get("target_handle")
        )
    ]
    if recovery_count == 0 and recovery_reads:
        recovery_count = len(recovery_reads)
    follow_loaded = bool(follow_next_reads)
    expanded_count = int((follow_next_reads or {}).get("expanded_count") or 0)
    selected_count = int((follow_next_reads or {}).get("selected_count") or 0)
    failed_count = int((follow_next_reads or {}).get("failed_count") or 0)
    useful_count = int(
        (follow_next_reads or {}).get("reviewed_usable_recovery_evidence_count")
        or (follow_next_reads or {}).get("usable_recovery_evidence_count")
        or 0
    )
    not_useful_count = int((follow_next_reads or {}).get("reviewed_not_useful_recovery_count") or 0)
    unreviewed_count = int((follow_next_reads or {}).get("still_unreviewed_recovery_count") or 0)
    if recovery_count <= 0:
        status = "no_recovery_targets"
    elif not follow_loaded:
        status = "follow_next_reads_not_loaded"
    elif expanded_count > 0:
        status = "recovery_opened"
    else:
        status = "recovery_targets_unopened"
    return {
        "schema_version": 1,
        "status": status,
        "recovery_target_count": recovery_count,
        "promoted_next_read_count": promoted_count,
        "top_recovery_targets": [
            {
                "target_handle": f"recovery_target:{index:03d}",
                "opened_status": "opened" if follow_loaded and expanded_count else "still_unopened",
            }
            for index, _read in enumerate(recovery_reads[:5], 1)
        ],
        "follow_next_reads_loaded": follow_loaded,
        "follow_next_reads_selected_count": selected_count,
        "follow_next_reads_expanded_count": expanded_count,
        "follow_next_reads_failed_count": failed_count,
        "useful_recovery_evidence_count": useful_count,
        "not_useful_recovery_evidence_count": not_useful_count,
        "still_unreviewed_recovery_count": unreviewed_count,
        "accountability_boundary": "recovery targets are not final support until opened or explicitly deferred",
        "privacy": _privacy_flags(),
    }


def _eval_facet_hooks(
    *,
    handle_ledger: dict[str, Any],
    proof_action_coverage: dict[str, Any],
    answer_gate: dict[str, Any],
    recovery_read_accountability: dict[str, Any],
    no_evidence: dict[str, Any],
) -> dict[str, Any]:
    counts = handle_ledger.get("counts") or {}
    missing_proof = list(proof_action_coverage.get("missing_required_buckets") or [])
    blocked = set(answer_gate.get("blocked_facets") or [])
    return {
        "schema_version": 1,
        "core_answer": {
            "status": "scoped_no_evidence" if no_evidence.get("needed") else "source_boundary_required",
            "blocked": "recovery_not_opened" in blocked,
        },
        "exact_handle": {
            "candidate_count": int(counts.get("candidate") or 0),
            "supporting_count": int(counts.get("supporting") or 0),
            "unsupported_extra_count": int(counts.get("unsupported_extra") or 0),
        },
        "proof_command_source_action": {
            "missing_required_buckets": missing_proof,
            "blocked": bool(missing_proof),
        },
        "adjacent_status": {
            "recovery_status": recovery_read_accountability.get("status"),
            "opened_count": int(recovery_read_accountability.get("follow_next_reads_expanded_count") or 0),
        },
        "unsupported_extra_handle_risk": {
            "present": int(counts.get("unsupported_extra") or 0) > 0,
            "count": int(counts.get("unsupported_extra") or 0),
        },
        "missing_evidence_risk": {
            "present": bool(missing_proof) or "recovery_not_opened" in blocked,
            "blocked_facets": list(answer_gate.get("blocked_facets") or [])[:8],
        },
    }


def _anchor_families(candidate: dict[str, Any], ledger_item: dict[str, Any]) -> list[str]:
    labels = []
    reason_codes = [str(value) for value in candidate.get("reason_codes") or []]
    if any("phrase" in value for value in reason_codes):
        labels.append("quoted_or_phrase_anchor")
    if ledger_item.get("candidate_id") or candidate.get("candidate_id"):
        labels.append("candidate_ref")
    membership = ledger_item.get("membership") if isinstance(ledger_item.get("membership"), dict) else {}
    if membership.get("evidence_packet_children"):
        labels.append("evidence_packet_child")
    if membership.get("source_windows"):
        labels.append("source_window")
    if membership.get("omitted_window_recovery"):
        labels.append("recovery_ref")
    if ledger_item.get("query_need_labels"):
        labels.append("query_need")
    return sorted(set(labels)) or ["candidate_ref"]


def _ledger_handle_id(item: dict[str, Any]) -> str:
    ledger_id = str(item.get("ledger_id") or "")
    return f"ledger:{ledger_id}" if ledger_id else "ledger:unknown"


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _privacy_flags() -> dict[str, bool]:
    return {
        "raw_private_text_included": False,
        "raw_paths_included": False,
        "raw_session_ids_included": False,
        "source_lines_included": False,
        "vectors_or_indexes_included": False,
    }
