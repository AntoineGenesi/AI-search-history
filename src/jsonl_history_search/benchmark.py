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
REQUIRED_BUDGETS = {"max_tool_calls", "max_files_opened", "max_context_bytes", "target_top_k"}

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
        missing_stages = REQUIRED_STAGES - stages
        if missing_stages:
            errors.append(f"{label}: missing broad candidate stages {sorted(missing_stages)}")
        if stages == {"exact_phrase"}:
            errors.append(f"{label}: exact-only candidate generation is not allowed")

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

    missing_categories = REQUIRED_CATEGORIES - seen_categories
    if missing_categories:
        errors.append(f"missing benchmark categories: {sorted(missing_categories)}")

    missing_failure_modes = REQUIRED_FAILURE_MODES - seen_failure_modes
    if missing_failure_modes:
        errors.append(f"missing required failure modes: {sorted(missing_failure_modes)}")

    private_hits = find_private_pattern_hits(payload)
    for hit in private_hits:
        errors.append(f"private pattern found at {hit}")

    return errors


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
