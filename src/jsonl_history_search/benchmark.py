from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


REQUIRED_CATEGORIES = {"success", "partial_ambiguous", "no_evidence", "failure"}
REQUIRED_STAGES = {
    "exact_phrase",
    "normalized_terms",
    "typo_fuzzy",
    "synonym_paraphrase",
    "artifact_path",
    "cwd_project",
    "date_window",
    "role_filter",
    "semantic_local",
    "newest_first_shortlist",
}
REQUIRED_FAILURE_MODES = {
    "wrong_root",
    "broad_recursive_walk",
    "ignored_date_hint",
    "ignored_chat_project_hint",
    "exact_phrase_miss",
    "over_reading",
    "too_many_tool_calls",
    "no_role_filter",
    "no_newest_first_shortlist",
    "false_confident_answer",
}
REQUIRED_LINEAGE_RELATIONSHIPS = {
    "source_project",
    "extracted_project",
    "renamed_duplicate",
    "sibling_project",
}
REQUIRED_BUDGETS = {"max_tool_calls", "max_files_opened", "max_context_bytes", "target_top_k"}
VALID_EVIDENCE_STATUSES = {"private_mined", "needs_followup"}

PRIVATE_PATTERNS = [
    re.compile(r"/home/[A-Za-z0-9_.-]+"),
    re.compile(r"/Users/[A-Za-z0-9_.-]+"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\", re.IGNORECASE),
    re.compile(r"\bcontributor\b", re.IGNORECASE),
    re.compile(r"\bprojects\b", re.IGNORECASE),
]


def load_benchmark_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_benchmark_fixture(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if payload.get("mining_protocol") != "jsh-002-broad-recall-v1":
        errors.append("mining_protocol must be jsh-002-broad-recall-v1")

    declared_stages = set(payload.get("required_candidate_generation_stages") or [])
    missing_declared = REQUIRED_STAGES - declared_stages
    if missing_declared:
        errors.append(f"missing declared candidate generation stages: {sorted(missing_declared)}")
    declared_lineage = set(payload.get("required_lineage_relationships") or [])
    missing_lineage = REQUIRED_LINEAGE_RELATIONSHIPS - declared_lineage
    if missing_lineage:
        errors.append(f"missing declared lineage relationships: {sorted(missing_lineage)}")

    privacy = payload.get("privacy")
    if not isinstance(privacy, dict):
        errors.append("privacy metadata is required")
    elif privacy.get("raw_private_text_committed") is not False:
        errors.append("privacy.raw_private_text_committed must be false")

    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty list")
        return errors

    seen_categories: set[str] = set()
    seen_failure_modes: set[str] = set()
    seen_stages: set[str] = set()
    seen_lineage_relationships: set[str] = set()
    for index, case in enumerate(cases):
        label = case.get("case_id") or f"case[{index}]"
        if not isinstance(case.get("case_id"), str) or not case["case_id"]:
            errors.append(f"{label}: case_id is required")

        category = case.get("category")
        if category not in REQUIRED_CATEGORIES:
            errors.append(f"{label}: invalid category {category!r}")
        else:
            seen_categories.add(category)

        stages = set(case.get("candidate_generation_stages") or [])
        unknown_stages = stages - REQUIRED_STAGES
        if not stages:
            errors.append(f"{label}: candidate_generation_stages must be a non-empty observed-stage list")
        if unknown_stages:
            errors.append(f"{label}: unknown candidate generation stages {sorted(unknown_stages)}")
        seen_stages.update(stages)

        budgets = case.get("budgets")
        if not isinstance(budgets, dict):
            errors.append(f"{label}: budgets are required")
        else:
            missing_budgets = REQUIRED_BUDGETS - set(budgets)
            if missing_budgets:
                errors.append(f"{label}: missing budgets {sorted(missing_budgets)}")
            for key in REQUIRED_BUDGETS & set(budgets):
                if not isinstance(budgets[key], int) or budgets[key] <= 0:
                    errors.append(f"{label}: budget {key} must be a positive integer")

        failure_modes = case.get("failure_modes")
        if not isinstance(failure_modes, list):
            errors.append(f"{label}: failure_modes must be a list")
        else:
            unknown_modes = set(failure_modes) - REQUIRED_FAILURE_MODES
            if unknown_modes:
                errors.append(f"{label}: unknown failure modes {sorted(unknown_modes)}")
            seen_failure_modes.update(failure_modes)

        if not isinstance(case.get("query_paraphrase"), str) or not case["query_paraphrase"]:
            errors.append(f"{label}: query_paraphrase is required")
        if not isinstance(case.get("transcript_pattern_paraphrase"), str) or not case["transcript_pattern_paraphrase"]:
            errors.append(f"{label}: transcript_pattern_paraphrase is required")
        if not isinstance(case.get("sanitized_gold_refs"), list) or not case["sanitized_gold_refs"]:
            errors.append(f"{label}: sanitized_gold_refs are required")
        validate_evidence_refs(case, label, errors)
        seen_lineage_relationships.update(validate_lineage_relationships(case, label, errors))

    missing_categories = REQUIRED_CATEGORIES - seen_categories
    if missing_categories:
        errors.append(f"missing benchmark categories: {sorted(missing_categories)}")

    followup = validate_coverage_followup(payload, errors)
    missing_stages = REQUIRED_STAGES - seen_stages - set(followup.get("candidate_generation_stages", []))
    if missing_stages:
        errors.append(f"missing required stage coverage or follow-up markers: {sorted(missing_stages)}")

    missing_failure_modes = REQUIRED_FAILURE_MODES - seen_failure_modes - set(followup.get("failure_modes", []))
    if missing_failure_modes:
        errors.append(f"missing required failure modes or follow-up markers: {sorted(missing_failure_modes)}")

    missing_lineage_cases = REQUIRED_LINEAGE_RELATIONSHIPS - seen_lineage_relationships
    if missing_lineage_cases:
        errors.append(f"missing lineage relationship fixture coverage: {sorted(missing_lineage_cases)}")

    private_hits = find_private_pattern_hits(payload)
    for hit in private_hits:
        errors.append(f"private pattern found at {hit}")

    return errors


def validate_lineage_relationships(case: dict[str, Any], label: str, errors: list[str]) -> set[str]:
    relationships = case.get("lineage_relationships")
    if relationships is None:
        return set()
    if not isinstance(relationships, list) or not relationships:
        errors.append(f"{label}: lineage_relationships must be a non-empty list when present")
        return set()
    seen: set[str] = set()
    for index, relationship in enumerate(relationships):
        item_label = f"{label}.lineage_relationships[{index}]"
        if not isinstance(relationship, dict):
            errors.append(f"{item_label}: entry must be an object")
            continue
        relationship_type = relationship.get("relationship_type")
        if relationship_type not in REQUIRED_LINEAGE_RELATIONSHIPS:
            errors.append(f"{item_label}: unknown relationship_type {relationship_type!r}")
            continue
        seen.add(relationship_type)
        for key in ("project_role", "evidence_pattern"):
            if not isinstance(relationship.get(key), str) or not relationship[key]:
                errors.append(f"{item_label}: {key} is required")
        confidence = relationship.get("expected_confidence")
        if confidence not in {"low", "medium", "high"}:
            errors.append(f"{item_label}: expected_confidence must be low, medium, or high")
    return seen


def validate_coverage_followup(payload: dict[str, Any], errors: list[str]) -> dict[str, list[str]]:
    followup = payload.get("coverage_needs_followup")
    if not isinstance(followup, dict):
        errors.append("coverage_needs_followup is required")
        return {"candidate_generation_stages": [], "failure_modes": []}

    stage_items = followup.get("candidate_generation_stages")
    mode_items = followup.get("failure_modes")
    if not isinstance(stage_items, list):
        errors.append("coverage_needs_followup.candidate_generation_stages must be a list")
        stage_items = []
    if not isinstance(mode_items, list):
        errors.append("coverage_needs_followup.failure_modes must be a list")
        mode_items = []

    unknown_stages = set(stage_items) - REQUIRED_STAGES
    unknown_modes = set(mode_items) - REQUIRED_FAILURE_MODES
    if unknown_stages:
        errors.append(f"unknown follow-up candidate generation stages: {sorted(unknown_stages)}")
    if unknown_modes:
        errors.append(f"unknown follow-up failure modes: {sorted(unknown_modes)}")
    return {"candidate_generation_stages": list(stage_items), "failure_modes": list(mode_items)}


def validate_evidence_refs(case: dict[str, Any], label: str, errors: list[str]) -> None:
    status = case.get("evidence_status")
    if status not in VALID_EVIDENCE_STATUSES:
        errors.append(f"{label}: evidence_status must be one of {sorted(VALID_EVIDENCE_STATUSES)}")
        return

    refs = case.get("sanitized_gold_refs")
    if not isinstance(refs, list):
        return
    for ref in refs:
        if not isinstance(ref, dict):
            errors.append(f"{label}: sanitized_gold_refs entries must be objects")
            continue
        ref_id = ref.get("ref_id")
        ref_kind = ref.get("ref_kind")
        if not isinstance(ref_id, str) or not ref_id:
            errors.append(f"{label}: ref_id is required")
            continue
        if "private-evidence-candidates.md#" in ref_id and "#real-" not in ref_id:
            errors.append(f"{label}: placeholder private evidence ref is not allowed: {ref_id}")
        if status == "private_mined":
            if ref_kind != "private_local_path_line":
                errors.append(f"{label}: private_mined refs must use private_local_path_line")
            if not ref_id.startswith("private-evidence-candidates.md#real-"):
                errors.append(f"{label}: private_mined refs must point at a real private evidence ID")
        if status == "needs_followup":
            if ref_kind != "followup_required":
                errors.append(f"{label}: needs_followup refs must use followup_required")


def find_private_pattern_hits(value: Any, *, prefix: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            hits.extend(find_private_pattern_hits(item, prefix=f"{prefix}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(find_private_pattern_hits(item, prefix=f"{prefix}[{index}]"))
    elif isinstance(value, str):
        for pattern in PRIVATE_PATTERNS:
            if pattern.search(value):
                hits.append(prefix)
                break
    return hits
