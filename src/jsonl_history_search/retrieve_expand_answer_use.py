from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import timezone
from pathlib import Path
from typing import Any

from .compact_handoff_usefulness import (
    build_live_packet_bundle,
    markdown_cell,
    no_evidence_scope_limited,
    normalize_targets,
    targets_from_next_reads,
    targets_from_source_refs,
    targets_from_windows,
    targets_present,
    unsupported_claims_false,
)
from .source_expansion import ExpansionRequest, build_source_expansion_payload
from .sources import Limits, build_source_plan


SCHEMA_VERSION = 1
DEFAULT_FIXTURE = Path("benchmarks/retrieve_expand_answer_use/synthetic_cases.json")
REQUIRED_QUESTION_FAMILIES = [
    "why",
    "workflow_failure",
    "when_user_said",
    "what_was_i_talking_about",
]
REQUIRED_DEFER_CLASSES = [
    "false_presupposition_unavailable_evidence",
    "ambiguity_insufficient_compact_context",
    "next_record_or_window_dependency",
]
SCORE_LABELS = {
    "answerable_from_packet",
    "answerable_after_expansion",
    "correctly_deferred",
    "missing_or_wrong_evidence",
    "malformed_or_missing_action",
    "fixture_or_execution_failure",
}
FAILURE_TAXONOMY_BUCKETS = [
    "query_planning",
    "source_planning_candidate_generation",
    "source_window_selection",
    "expansion_action_coverage",
    "packet_fields",
    "answer_guidance",
    "no_evidence_defer_wording",
]
NEXT_DECISION_PRIORITY = [
    "query_planning",
    "source_planning_candidate_generation",
    "source_window_selection",
    "expansion_action_coverage",
    "packet_fields",
    "answer_guidance",
    "no_evidence_defer_wording",
]
ALL_ACTIONABLE_DECISION = "proceed_to_source_session_date_diversity_tool_delta"
CLAIM_BOUNDARIES = {
    "retrieve_expand_answer_use_eval_only": True,
    "synthetic_public_fixture_only": True,
    "exact_emitted_expansion_argv_replayed": True,
    "token_savings_claim_supported": False,
    "final_answer_quality_claim_supported": False,
    "broad_retrieval_quality_claim_supported": False,
    "all_history_completeness_claim_supported": False,
    "default_promotion_supported": False,
    "llm_judge_gate_used": False,
    "private_transcript_text_used": False,
    "persistent_index_or_vector_used": False,
}


def build_retrieve_expand_answer_use_eval_payload(fixture_path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    return {"retrieve_expand_answer_use_eval": build_retrieve_expand_answer_use_eval(fixture_path)}


def build_retrieve_expand_answer_use_eval(fixture_path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    fixture_path = fixture_path.expanduser()
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return invalid_fixture_report(fixture_path, f"fixture_read_failed:{exc}")
    except json.JSONDecodeError as exc:
        return invalid_fixture_report(fixture_path, f"fixture_json_invalid:{exc}")

    cases = fixture.get("cases")
    if not isinstance(cases, list) or not cases:
        return invalid_fixture_report(fixture_path, "fixture_cases_missing")

    rows = [evaluate_case(case, fixture_path=fixture_path) for case in cases]
    status = "ok"
    if any(row["score_label"] == "fixture_or_execution_failure" for row in rows):
        status = "inconclusive"
    if any(row["score_label"] == "invalid_fixture" for row in rows):
        status = "invalid_fixture"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "data_source": "synthetic_public_committed_only",
        "fixture": str(fixture_path),
        "retrieval_mode_required": "fielded-bm25",
        "question_family_coverage": question_family_coverage(rows),
        "defer_case_coverage": defer_case_coverage(rows),
        "rows": rows,
        "summary": summarize_rows(rows),
        "failure_taxonomy_buckets": FAILURE_TAXONOMY_BUCKETS,
        "selected_next_product_decision": select_next_product_decision(rows),
        "claim_boundaries": CLAIM_BOUNDARIES,
    }


def invalid_fixture_report(fixture_path: Path, reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "invalid_fixture",
        "data_source": "synthetic_public_committed_only",
        "fixture": str(fixture_path),
        "retrieval_mode_required": "fielded-bm25",
        "question_family_coverage": {family: False for family in REQUIRED_QUESTION_FAMILIES},
        "defer_case_coverage": {name: False for name in REQUIRED_DEFER_CLASSES},
        "rows": [],
        "summary": {
            "case_count": 0,
            "actionable_or_deferred_count": 0,
            "failure_count": 0,
            "invalid_fixture_count": 1,
            "invalid_fixture_reasons": [reason],
        },
        "failure_taxonomy_buckets": FAILURE_TAXONOMY_BUCKETS,
        "selected_next_product_decision": "fixture_invalid_rerun_after_repair",
        "claim_boundaries": CLAIM_BOUNDARIES,
    }


def evaluate_case(case: dict[str, Any], *, fixture_path: Path) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "missing_case_id")
    expected_score = str(case.get("expected_score_label") or "")
    row_base = {
        "case_id": case_id,
        "question_family": str(case.get("question_family") or ""),
        "defer_class": str(case.get("defer_class") or ""),
        "command_surface": str(case.get("command_surface") or ""),
        "query": str(case.get("query") or ""),
        "expected_score_label": expected_score,
        "required_action_modes": [str(item) for item in case.get("required_action_modes") or []],
        "expected_source_targets": [str(item) for item in case.get("expected_source_targets") or []],
    }
    invalid_reason = fixture_invalid_reason(case)
    if invalid_reason:
        return {
            **row_base,
            "score_label": "invalid_fixture",
            "score_matches_expected": False,
            "failure_taxonomy": "fixture_or_execution_failure",
            "product_decision_signal": "fixture_invalid_rerun_after_repair",
            "invalid_fixture_reason": invalid_reason,
            "packet_observation": {},
            "expansion_observation": {},
        }

    try:
        packet_bundle = build_live_packet_bundle(case, fixture_path=fixture_path)
        packet_observation = observe_packet(packet_bundle, case)
        expansion_observation = replay_expansion_actions(packet_bundle, case)
    except Exception as exc:  # pragma: no cover - retained as eval diagnostics.
        return {
            **row_base,
            "score_label": "fixture_or_execution_failure",
            "score_matches_expected": False,
            "failure_taxonomy": "fixture_or_execution_failure",
            "product_decision_signal": "fixture_or_eval_execution_needs_repair",
            "execution_error": exc.__class__.__name__,
            "packet_observation": {},
            "expansion_observation": {},
        }

    score_label = score_case(case, packet_observation, expansion_observation)
    failure_taxonomy = failure_taxonomy_for_score(case, score_label, packet_observation, expansion_observation)
    return {
        **row_base,
        "score_label": score_label,
        "score_matches_expected": score_label == expected_score,
        "failure_taxonomy": failure_taxonomy,
        "product_decision_signal": product_decision_for_failure(failure_taxonomy),
        "packet_observation": packet_observation,
        "expansion_observation": expansion_observation,
    }


def fixture_invalid_reason(case: dict[str, Any]) -> str | None:
    if not case.get("case_id"):
        return "missing_case_id"
    if case.get("question_family") not in [*REQUIRED_QUESTION_FAMILIES, "defer_control"]:
        return "invalid_question_family"
    if case.get("command_surface") not in {"find", "semantic-debug"}:
        return "invalid_command_surface"
    if not case.get("query"):
        return "missing_query"
    if case.get("expected_score_label") not in SCORE_LABELS:
        return "invalid_expected_score_label"
    if not isinstance(case.get("gold_markers"), list):
        return "gold_markers_missing"
    if case.get("expected_score_label") != "correctly_deferred" and not case.get("gold_markers"):
        return "answerable_case_missing_gold_markers"
    return None


def observe_packet(packet_bundle: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    packet = packet_bundle["packet"]
    compact = packet_bundle["compact"]
    locator = packet_bundle["locator"]
    repo_root = packet_bundle["repo_root"]
    source_refs = list(compact.get("source_refs") or [])
    source_windows = list(compact.get("source_windows") or [])
    next_reads = list(compact.get("next_reads") or [])
    no_evidence = compact.get("no_evidence") or {}
    claim_boundaries = compact.get("claim_boundaries") or {}
    query_plan = locator.get("query_plan") or {}
    expected_targets = [str(item) for item in case.get("expected_source_targets") or []]
    source_ref_targets = normalize_targets(targets_from_source_refs(source_refs), repo_root=repo_root)
    window_targets = normalize_targets(targets_from_windows(source_windows), repo_root=repo_root)
    next_read_targets = normalize_targets(targets_from_next_reads(next_reads), repo_root=repo_root)
    action_count = sum(len(item.get("expansion_actions") or []) for item in [*source_windows, *next_reads])
    return {
        "packet_status": packet.get("status"),
        "compact_status": compact.get("status"),
        "query_plan_primary_intent": query_plan.get("primary_intent"),
        "query_plan_matches_family": query_plan_matches_family(str(case.get("question_family") or ""), query_plan),
        "source_ref_count": len(source_refs),
        "source_window_count": len(source_windows),
        "next_read_count": len(next_reads),
        "expansion_action_count": action_count,
        "expected_source_refs_present": targets_present(expected_targets, source_ref_targets),
        "expected_source_windows_present": targets_present(expected_targets, window_targets),
        "expected_next_reads_present": targets_present(expected_targets, next_read_targets),
        "no_evidence_scope_limited": no_evidence_scope_limited(no_evidence, claim_boundaries),
        "claim_boundaries_honest": unsupported_claims_false(claim_boundaries),
        "packet_has_decision_fields": packet_has_decision_fields(compact, locator),
    }


def query_plan_matches_family(question_family: str, query_plan: dict[str, Any]) -> bool:
    if question_family == "defer_control":
        return True
    return query_plan.get("primary_intent") == question_family


def packet_has_decision_fields(compact: dict[str, Any], locator: dict[str, Any]) -> bool:
    return all(
        [
            compact.get("source_refs") is not None,
            compact.get("source_windows") is not None,
            compact.get("next_reads") is not None,
            compact.get("no_evidence") is not None,
            compact.get("claim_boundaries") is not None,
            locator.get("query_plan") is not None,
        ]
    )


def replay_expansion_actions(packet_bundle: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    compact = packet_bundle["compact"]
    repo_root = packet_bundle["repo_root"]
    required_modes = {str(item) for item in case.get("required_action_modes") or []}
    gold_markers = [str(item).lower() for item in case.get("gold_markers") or []]
    actions = collect_expansion_actions(compact)
    replayed: list[dict[str, Any]] = []
    matched_modes: set[str] = set()
    marker_matches: set[str] = set()
    gold_targets: set[str] = set()
    malformed_count = 0
    execution_failure_count = 0
    for action in actions:
        argv = [str(item) for item in action.get("argv") or []]
        mode = str(action.get("mode") or "")
        try:
            payload = replay_read_more_argv(argv)
        except (SystemExit, ValueError, OSError) as exc:
            malformed_count += 1
            replayed.append({"mode": mode, "status": "argv_replay_failed", "error": exc.__class__.__name__})
            continue
        expansion = payload["source_expansion"]
        status = str(expansion.get("status") or "")
        if status != "expanded":
            execution_failure_count += 1
        record_texts = [str(record.get("text") or "") for record in expansion.get("records") or []]
        text_blob = "\n".join(record_texts).lower()
        matched = [marker for marker in gold_markers if marker in text_blob]
        if matched:
            marker_matches.update(matched)
            matched_modes.add(mode)
            gold_targets.update(
                normalize_targets(
                    [str(record.get("target")) for record in expansion.get("records") or [] if record.get("target")],
                    repo_root=repo_root,
                )
            )
        replayed.append(
            {
                "mode": mode,
                "status": status,
                "target": normalize_target(str(expansion.get("requested_target") or ""), repo_root=repo_root),
                "records_included": len(expansion.get("records") or []),
                "gold_marker_match_count": len(matched),
            }
        )
    required_modes_available = required_modes.issubset({str(action.get("mode") or "") for action in actions})
    required_modes_matched = required_modes.issubset(matched_modes) if required_modes else bool(marker_matches)
    return {
        "action_count": len(actions),
        "malformed_action_count": malformed_count,
        "execution_failure_count": execution_failure_count,
        "required_modes_available": required_modes_available,
        "required_modes_matched_gold": required_modes_matched,
        "gold_marker_match_count": len(marker_matches),
        "gold_target_count": len(gold_targets),
        "action_modes_replayed": sorted({item.get("mode") for item in replayed if item.get("mode")}),
        "exact_argv_replayed": bool(actions) and malformed_count == 0,
        "replayed_actions": replayed,
    }


def collect_expansion_actions(compact: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for container_name in ("source_windows", "next_reads"):
        for item in compact.get(container_name) or []:
            actions.extend(action for action in item.get("expansion_actions") or [] if isinstance(action, dict))
    return actions


def replay_read_more_argv(argv: list[str]) -> dict[str, Any]:
    args = parse_read_more_argv(argv)
    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd().resolve()
    roots = [Path(root).expanduser().resolve() for root in args.root]
    limits = Limits(
        max_files=args.hard_cap_files,
        max_bytes=args.hard_cap_bytes,
        max_matches=20,
        max_elapsed=15.0,
        hard_cap_files=args.hard_cap_files,
        hard_cap_bytes=args.hard_cap_bytes,
    )
    plan = build_source_plan(
        cwd=cwd,
        codex_home=Path(args.codex_home).expanduser().resolve(),
        claude_home=Path(args.claude_home).expanduser().resolve(),
        roots=roots,
        allow_arbitrary_root=args.allow_arbitrary_root,
        limits=limits,
    )
    return build_source_expansion_payload(
        request=ExpansionRequest(
            target=args.target,
            mode=args.mode,
            before=args.before,
            after=args.after,
            next_count=args.next_count,
            max_records=args.max_records,
        ),
        plan=plan,
        default_tz=timezone.utc,
        cwd=cwd,
    )


def parse_read_more_argv(argv: list[str]) -> argparse.Namespace:
    if not argv or argv[0] != "read-more":
        raise ValueError("argv_must_start_with_read_more")
    parser = argparse.ArgumentParser(prog="read-more", add_help=False)
    parser.add_argument("command")
    parser.add_argument("target")
    parser.add_argument("--mode", choices=("record", "window", "next"), default="record")
    parser.add_argument("--before", type=int, default=2)
    parser.add_argument("--after", type=int, default=2)
    parser.add_argument("--next-count", type=int, default=3)
    parser.add_argument("--max-records", type=int, default=20)
    parser.add_argument("--format", choices=("json", "markdown", "text"), default="json")
    parser.add_argument("--root", action="append", default=[])
    parser.add_argument("--allow-arbitrary-root", action="store_true")
    parser.add_argument("--hard-cap-files", type=int, default=20)
    parser.add_argument("--hard-cap-bytes", type=int, default=200_000)
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--codex-home", default="~/.codex")
    parser.add_argument("--claude-home", default="~/.claude")
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError(f"unsupported_read_more_argv:{unknown[0]}")
    if args.command != "read-more":
        raise ValueError("invalid_read_more_command")
    if not args.root:
        raise ValueError("read_more_argv_missing_root")
    return args


def score_case(case: dict[str, Any], packet: dict[str, Any], expansion: dict[str, Any]) -> str:
    expected = str(case.get("expected_score_label") or "")
    if expected == "correctly_deferred":
        if str(case.get("defer_class") or "") == "ambiguity_insufficient_compact_context":
            return "correctly_deferred" if ambiguity_deferred(packet, expansion) else "missing_or_wrong_evidence"
        return "correctly_deferred" if packet.get("no_evidence_scope_limited") else "missing_or_wrong_evidence"
    if packet_gold_answerable(case, packet):
        return "answerable_from_packet"
    if expansion.get("malformed_action_count") or not expansion.get("required_modes_available"):
        return "malformed_or_missing_action"
    if expansion.get("execution_failure_count"):
        return "fixture_or_execution_failure"
    if expansion.get("gold_marker_match_count") and expansion.get("required_modes_matched_gold"):
        return "answerable_after_expansion"
    return "missing_or_wrong_evidence"


def ambiguity_deferred(packet: dict[str, Any], expansion: dict[str, Any]) -> bool:
    return (
        packet.get("packet_has_decision_fields") is True
        and packet.get("claim_boundaries_honest") is True
        and expansion.get("gold_marker_match_count", 0) == 0
    )


def packet_gold_answerable(case: dict[str, Any], packet: dict[str, Any]) -> bool:
    if case.get("allow_packet_only_answer") is not True:
        return False
    return packet.get("expected_source_refs_present") and packet.get("packet_has_decision_fields")


def failure_taxonomy_for_score(
    case: dict[str, Any],
    score_label: str,
    packet: dict[str, Any],
    expansion: dict[str, Any],
) -> str | None:
    if score_label in {"answerable_from_packet", "answerable_after_expansion", "correctly_deferred"}:
        return None
    if score_label == "malformed_or_missing_action":
        return "expansion_action_coverage"
    if score_label == "fixture_or_execution_failure":
        return "fixture_or_execution_failure"
    if not packet.get("query_plan_matches_family"):
        return "query_planning"
    if not packet.get("expected_source_refs_present") and case.get("expected_score_label") != "correctly_deferred":
        return "source_planning_candidate_generation"
    if not packet.get("expected_source_windows_present") and not packet.get("expected_next_reads_present"):
        return "source_window_selection"
    if not packet.get("packet_has_decision_fields") or not packet.get("claim_boundaries_honest"):
        return "packet_fields"
    if case.get("expected_score_label") == "correctly_deferred":
        return "no_evidence_defer_wording"
    if expansion.get("gold_marker_match_count", 0) == 0:
        return "source_window_selection"
    return "answer_guidance"


def product_decision_for_failure(failure_taxonomy: str | None) -> str:
    if failure_taxonomy is None:
        return "none"
    if failure_taxonomy in FAILURE_TAXONOMY_BUCKETS:
        return failure_taxonomy
    return "fixture_or_eval_execution_needs_repair"


def normalize_target(target: str, *, repo_root: Path) -> str:
    if not target:
        return ""
    normalized = normalize_targets([target], repo_root=repo_root)
    return normalized[0] if normalized else target


def question_family_coverage(rows: list[dict[str, Any]]) -> dict[str, bool]:
    families = {row.get("question_family") for row in rows}
    return {family: family in families for family in REQUIRED_QUESTION_FAMILIES}


def defer_case_coverage(rows: list[dict[str, Any]]) -> dict[str, bool]:
    classes = {row.get("defer_class") for row in rows}
    return {name: name in classes for name in REQUIRED_DEFER_CLASSES}


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    score_counts = Counter(row["score_label"] for row in rows)
    taxonomy_counts = Counter(row["failure_taxonomy"] for row in rows if row.get("failure_taxonomy"))
    decision_counts = Counter(row["product_decision_signal"] for row in rows if row.get("product_decision_signal") != "none")
    family_counts = Counter(row["question_family"] for row in rows)
    return {
        "case_count": len(rows),
        "actionable_or_deferred_count": sum(
            score_counts.get(label, 0)
            for label in ("answerable_from_packet", "answerable_after_expansion", "correctly_deferred")
        ),
        "failure_count": sum(
            score_counts.get(label, 0)
            for label in ("missing_or_wrong_evidence", "malformed_or_missing_action", "fixture_or_execution_failure")
        ),
        "invalid_fixture_count": score_counts.get("invalid_fixture", 0),
        "score_label_counts": dict(sorted(score_counts.items())),
        "failure_taxonomy_counts": dict(sorted(taxonomy_counts.items())),
        "product_decision_signal_counts": dict(sorted(decision_counts.items())),
        "question_family_counts": dict(sorted(family_counts.items())),
        "exact_argv_replayed_count": sum(
            1 for row in rows if row.get("expansion_observation", {}).get("exact_argv_replayed") is True
        ),
    }


def select_next_product_decision(rows: list[dict[str, Any]]) -> str:
    signals = {row.get("product_decision_signal") for row in rows}
    for decision in NEXT_DECISION_PRIORITY:
        if decision in signals:
            return decision
    if any(row.get("score_label") in {"invalid_fixture", "fixture_or_execution_failure"} for row in rows):
        return "fixture_or_eval_execution_needs_repair"
    return ALL_ACTIONABLE_DECISION


def render_retrieve_expand_answer_use_markdown(payload: dict[str, Any]) -> str:
    report = payload["retrieve_expand_answer_use_eval"]
    summary = report["summary"]
    lines = [
        "# Retrieve Expand Answer-Use Eval",
        "",
        f"- Status: `{report['status']}`",
        f"- Data source: `{report['data_source']}`",
        f"- Retrieval mode: `{report['retrieval_mode_required']}`",
        f"- Selected next product decision: `{report['selected_next_product_decision']}`",
        "",
        "## Summary",
    ]
    for key in ("case_count", "actionable_or_deferred_count", "failure_count", "invalid_fixture_count", "exact_argv_replayed_count"):
        lines.append(f"- {key}: `{summary.get(key, 0)}`")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Case | Family | Surface | Score | Failure Taxonomy | Actions | Gold Markers | Decision Signal |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in report["rows"]:
        expansion = row.get("expansion_observation", {})
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(row["case_id"]),
                    markdown_cell(row["question_family"]),
                    markdown_cell(row["command_surface"]),
                    markdown_cell(row["score_label"]),
                    markdown_cell(row["failure_taxonomy"] or "none"),
                    markdown_cell(",".join(expansion.get("action_modes_replayed", []))),
                    markdown_cell(str(expansion.get("gold_marker_match_count", 0))),
                    markdown_cell(row["product_decision_signal"]),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Failure Taxonomy"])
    for bucket in report["failure_taxonomy_buckets"]:
        lines.append(f"- `{bucket}`")
    lines.extend(["", "## Claim Boundaries"])
    for key, value in report["claim_boundaries"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    return "\n".join(lines)
