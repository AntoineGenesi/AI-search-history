from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1

SPEAKER_ORIGINS = {
    "human_user",
    "coordinator_agent",
    "worker_subagent",
    "assistant_claim",
    "tool_action_output",
    "review_artifact",
    "task_report_text",
    "unknown",
}

REVIEW_STATUSES = {
    "synthetic_reviewed",
    "candidate_needs_context_review",
    "reviewed_real_history_gold",
    "rejected",
}

DIFFICULTIES = {"easy", "medium", "hard", "adversarial"}
REQUIRED_CASE_FIELDS = {
    "case_id",
    "family",
    "difficulty",
    "speaker_origin",
    "recipient",
    "source_origin",
    "evidence_origin",
    "timestamp_label",
    "window_label",
    "expected_refs",
    "must_find_refs",
    "allowed_near_misses",
    "false_positive_risks",
    "review_status",
    "privacy_flags",
    "downstream_applicability",
}
REQUIRED_REF_FIELDS = {"ref_id", "ref_kind", "origin"}
REQUIRED_PRIVACY_FLAGS = {
    "raw_private_text_committed",
    "raw_private_paths_committed",
    "local_usernames_committed",
    "raw_session_ids_committed",
    "raw_query_wording_committed",
    "vectors_or_indexes_committed",
}
REQUIRED_DOWNSTREAM_FLAGS = {
    "impact_benchmark",
    "timestamp_seek",
    "embedding_recall",
    "ranking_quality",
    "release_claims",
}
PRIVATE_TRUE_FLAGS = {
    "raw_private_text_committed",
    "raw_private_paths_committed",
    "local_usernames_committed",
    "raw_session_ids_committed",
    "raw_query_wording_committed",
    "vectors_or_indexes_committed",
}
UNSAFE_KEY_FRAGMENTS = {
    "snippet",
    "raw_text",
    "private_text",
    "raw_path",
    "private_path",
    "username",
    "session_id",
    "raw_session",
    "raw_query",
    "query_wording",
    "vector",
    "embedding",
    "index_path",
}
UNSAFE_VALUE_PATTERNS = (
    re.compile(r"(?i)(^|[\\/])\.private([\\/]|$)"),
    re.compile(r"(?i)(^|[\\/])worktrees([\\/]|$)"),
    re.compile(r"(?i)(^|[\\/])home[\\/]([^<][^\\/]+)"),
    re.compile(r"(?i)(^|[\\/])users[\\/]([^<][^\\/]+)"),
    re.compile(r"(?i)\b[a-f0-9]{24,}\b"),
    re.compile(r"(?i)\b[a-z0-9_-]*session[a-z0-9_-]*-[a-f0-9]{8,}\b"),
    re.compile(r"(?i)\.(?:f32|faiss|npy|npz|sqlite|db)(?:$|[?#])"),
)


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[str, ...]
    report: dict[str, Any]

    @property
    def ok(self) -> bool:
        return not self.errors


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_manifest_file(path: Path) -> ValidationResult:
    return validate_manifest(load_manifest(path), source_label=str(path))


def validate_manifest(payload: dict[str, Any], *, source_label: str = "<memory>") -> ValidationResult:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ValidationResult(
            errors=("manifest root must be a JSON object",),
            report=build_report([], ["manifest root must be a JSON object"], source_label),
        )

    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty list")
        cases = []

    assessments: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for index, case in enumerate(cases):
        case_errors: list[str] = []
        if not isinstance(case, dict):
            errors.append(f"cases[{index}] must be an object")
            continue
        case_id = require_nonempty_string(case, "case_id", case_errors, f"cases[{index}]")
        if case_id:
            if case_id in seen_case_ids:
                case_errors.append(f"{case_id}: duplicate case_id")
            seen_case_ids.add(case_id)
        missing = sorted(REQUIRED_CASE_FIELDS - set(case))
        if missing:
            case_errors.append(f"{case_label(case, index)}: missing required fields {missing}")

        validate_label(case, "difficulty", DIFFICULTIES, case_errors, index)
        validate_label(case, "speaker_origin", SPEAKER_ORIGINS, case_errors, index)
        validate_nonempty_string_fields(
            case,
            ("family", "recipient", "source_origin", "evidence_origin", "timestamp_label", "window_label"),
            case_errors,
            index,
        )
        validate_refs(case, "expected_refs", case_errors, index, require_nonempty=True)
        must_find_refs = validate_refs(case, "must_find_refs", case_errors, index, require_nonempty=True)
        expected_ref_ids = {
            ref.get("ref_id") for ref in case.get("expected_refs", []) if isinstance(ref, dict) and ref.get("ref_id")
        }
        for ref_id in must_find_refs:
            if ref_id not in expected_ref_ids:
                case_errors.append(f"{case_label(case, index)}: must_find_refs ref_id {ref_id!r} is not in expected_refs")
        validate_refs(case, "allowed_near_misses", case_errors, index, require_nonempty=False)
        validate_string_list(case, "false_positive_risks", case_errors, index, require_nonempty=True)
        validate_label(case, "review_status", REVIEW_STATUSES, case_errors, index)
        validate_privacy_flags(case, case_errors, index)
        validate_downstream_applicability(case, case_errors, index)
        case_errors.extend(find_unsafe_payload(case, path=case_label(case, index)))

        errors.extend(case_errors)
        assessments.append(
            {
                "case_id": case.get("case_id", f"cases[{index}]"),
                "speaker_origin": case.get("speaker_origin", "unknown"),
                "review_status": case.get("review_status", "unknown"),
                "expected_ref_count": len(case.get("expected_refs") or []),
                "must_find_ref_count": len(case.get("must_find_refs") or []),
                "allowed_near_miss_count": len(case.get("allowed_near_misses") or []),
                "error_count": len(case_errors),
            }
        )
    return ValidationResult(errors=tuple(errors), report=build_report(assessments, errors, source_label))


def validate_nonempty_string_fields(case: dict[str, Any], fields: tuple[str, ...], errors: list[str], index: int) -> None:
    for field in fields:
        require_nonempty_string(case, field, errors, case_label(case, index))


def validate_label(case: dict[str, Any], field: str, allowed: set[str], errors: list[str], index: int) -> None:
    value = case.get(field)
    if not isinstance(value, str) or not value:
        errors.append(f"{case_label(case, index)}: {field} must be a non-empty string")
    elif value not in allowed:
        errors.append(f"{case_label(case, index)}: {field} has unsupported value {value!r}")


def require_nonempty_string(case: dict[str, Any], field: str, errors: list[str], label: str) -> str:
    value = case.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}: {field} must be a non-empty string")
        return ""
    return value


def validate_refs(
    case: dict[str, Any],
    field: str,
    errors: list[str],
    index: int,
    *,
    require_nonempty: bool,
) -> set[str]:
    refs = case.get(field)
    ref_ids: set[str] = set()
    label = case_label(case, index)
    if not isinstance(refs, list):
        errors.append(f"{label}: {field} must be a list")
        return ref_ids
    if require_nonempty and not refs:
        errors.append(f"{label}: {field} must not be empty")
    for ref_index, ref in enumerate(refs):
        ref_label = f"{label}.{field}[{ref_index}]"
        if not isinstance(ref, dict):
            errors.append(f"{ref_label}: ref must be an object")
            continue
        missing = sorted(REQUIRED_REF_FIELDS - set(ref))
        if missing:
            errors.append(f"{ref_label}: missing required ref fields {missing}")
        for ref_field in REQUIRED_REF_FIELDS:
            value = ref.get(ref_field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{ref_label}: {ref_field} must be a non-empty string")
        origin = ref.get("origin")
        if isinstance(origin, str) and origin not in SPEAKER_ORIGINS:
            errors.append(f"{ref_label}: origin has unsupported value {origin!r}")
        ref_id = ref.get("ref_id")
        if isinstance(ref_id, str):
            if ref_id in ref_ids:
                errors.append(f"{ref_label}: duplicate ref_id {ref_id!r}")
            ref_ids.add(ref_id)
    return ref_ids


def validate_string_list(
    case: dict[str, Any],
    field: str,
    errors: list[str],
    index: int,
    *,
    require_nonempty: bool,
) -> None:
    value = case.get(field)
    label = case_label(case, index)
    if not isinstance(value, list):
        errors.append(f"{label}: {field} must be a list")
        return
    if require_nonempty and not value:
        errors.append(f"{label}: {field} must not be empty")
    for item_index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{label}.{field}[{item_index}] must be a non-empty string")


def validate_privacy_flags(case: dict[str, Any], errors: list[str], index: int) -> None:
    flags = case.get("privacy_flags")
    label = case_label(case, index)
    if not isinstance(flags, dict):
        errors.append(f"{label}: privacy_flags must be an object")
        return
    missing = sorted(REQUIRED_PRIVACY_FLAGS - set(flags))
    if missing:
        errors.append(f"{label}: privacy_flags missing required flags {missing}")
    for key in sorted(REQUIRED_PRIVACY_FLAGS & set(flags)):
        value = flags.get(key)
        if not isinstance(value, bool):
            errors.append(f"{label}: privacy_flags.{key} must be boolean")
        elif key in PRIVATE_TRUE_FLAGS and value:
            errors.append(f"{label}: privacy_flags.{key} must be false for committed artifacts")


def validate_downstream_applicability(case: dict[str, Any], errors: list[str], index: int) -> None:
    flags = case.get("downstream_applicability")
    label = case_label(case, index)
    if not isinstance(flags, dict):
        errors.append(f"{label}: downstream_applicability must be an object")
        return
    missing = sorted(REQUIRED_DOWNSTREAM_FLAGS - set(flags))
    if missing:
        errors.append(f"{label}: downstream_applicability missing required flags {missing}")
    for key in sorted(REQUIRED_DOWNSTREAM_FLAGS & set(flags)):
        if not isinstance(flags.get(key), bool):
            errors.append(f"{label}: downstream_applicability.{key} must be boolean")


def find_unsafe_payload(value: Any, *, path: str) -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            normalized_key = key_text.lower()
            if key_text not in REQUIRED_PRIVACY_FLAGS and key_text not in REQUIRED_DOWNSTREAM_FLAGS and any(
                fragment in normalized_key for fragment in UNSAFE_KEY_FRAGMENTS
            ):
                errors.append(f"{path}: unsafe field name {key_text!r} is not allowed in committed gold cases")
            errors.extend(find_unsafe_payload(item, path=f"{path}.{key_text}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(find_unsafe_payload(item, path=f"{path}[{index}]"))
    elif isinstance(value, str):
        for pattern in UNSAFE_VALUE_PATTERNS:
            if pattern.search(value):
                errors.append(f"{path}: unsafe private-looking value is not allowed")
                break
    return errors


def build_report(assessments: list[dict[str, Any]], errors: list[str], source_label: str) -> dict[str, Any]:
    speaker_counts = Counter(item["speaker_origin"] for item in assessments)
    review_counts = Counter(item["review_status"] for item in assessments)
    return {
        "real_history_gold": {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if not errors else "rejected",
            "source": source_label,
            "case_count": len(assessments),
            "speaker_origin_counts": {label: speaker_counts.get(label, 0) for label in sorted(SPEAKER_ORIGINS)},
            "review_status_counts": dict(sorted(review_counts.items())),
            "error_count": len(errors),
            "privacy_contract": {
                "raw_private_text_committed": False,
                "raw_private_paths_committed": False,
                "local_usernames_committed": False,
                "raw_session_ids_committed": False,
                "raw_query_wording_committed": False,
                "vectors_or_indexes_committed": False,
            },
            "cases": assessments,
        }
    }


def case_label(case: dict[str, Any], index: int) -> str:
    value = case.get("case_id")
    return value if isinstance(value, str) and value else f"cases[{index}]"


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def render_text(report: dict[str, Any]) -> str:
    payload = report["real_history_gold"]
    return f"OK: real-history gold contract valid ({payload['case_count']} cases checked)"
