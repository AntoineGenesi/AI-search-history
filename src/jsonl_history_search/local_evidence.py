from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
EVIDENCE_MODES = ("public", "internal")


def build_local_evidence_diagnostic_payload(args: Any) -> dict:
    return {
        "local_evidence_diagnostic": local_evidence_diagnostic(
            raw_run_path=Path(args.raw_run).expanduser(),
            evidence_mode=str(args.evidence_mode),
            limit=int(args.limit or 20),
        )
    }


def local_evidence_diagnostic(*, raw_run_path: Path, evidence_mode: str = "public", limit: int = 20) -> dict:
    if evidence_mode not in EVIDENCE_MODES:
        raise ValueError(f"evidence_mode must be one of {EVIDENCE_MODES}")
    raw = _read_json(raw_run_path)
    rows = _rows(raw)[: max(0, limit)]
    internal = evidence_mode == "internal"
    rendered_rows = [_diagnostic_row(row, index=index + 1, internal=internal) for index, row in enumerate(rows)]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "evidence_mode": evidence_mode,
        "mode_label": "internal/local product debugging" if internal else "public/export safe",
        "raw_run": _raw_run_descriptor(raw_run_path, raw, internal=internal),
        "rows": rendered_rows,
        "summary": _summary(rendered_rows),
        "privacy_contract": _privacy_contract(internal=internal),
        "boundary": {
            "internal_mode": (
                "May preserve raw local paths, source refs, line ranges, command traces, final-answer evidence, "
                "and pass/fail reasoning for local product diagnosis. Do not publish this output."
            ),
            "public_mode": (
                "Suppresses raw query text, raw private paths, exact line numbers, snippets, final-answer text, "
                "and pass/fail prose. Use this for public/export surfaces."
            ),
            "external_upload_performed": False,
        },
    }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(raw: dict) -> list[dict]:
    value = raw.get("rows")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _raw_run_descriptor(path: Path, raw: dict, *, internal: bool) -> dict:
    return {
        "path": str(path) if internal else "<local_raw_run_path>",
        "run_id": str(raw.get("run_id") or "unknown") if internal else _public_id(raw.get("run_id")),
        "row_count": len(_rows(raw)),
        "raw_path_included": internal,
    }


def _diagnostic_row(row: dict, *, index: int, internal: bool) -> dict:
    source_refs = _source_refs(row)
    commands = _commands(row)
    return {
        "case_id": str(row.get("case_id") or f"row-{index:03d}"),
        "query": _raw_or_redacted(row.get("query_text") or row.get("query") or row.get("prompt"), internal),
        "source_refs": [_source_ref(ref, internal=internal, index=ref_index + 1) for ref_index, ref in enumerate(source_refs)],
        "line_ranges": [_line_range(ref, internal=internal) for ref in source_refs],
        "command_family_trace": [_command_family(command, internal=internal) for command in commands],
        "final_answer": _raw_or_redacted(row.get("final_answer") or row.get("answer") or row.get("final_answer_text"), internal),
        "pass_fail_reasoning": _raw_or_redacted(
            row.get("pass_fail_reasoning") or row.get("reasoning") or row.get("evaluation_reason"),
            internal,
        ),
        "diagnosis_inputs": {
            "raw_query_available": bool(row.get("query_text") or row.get("query") or row.get("prompt")),
            "source_ref_count": len(source_refs),
            "command_count": len(commands),
            "final_answer_available": bool(row.get("final_answer") or row.get("answer") or row.get("final_answer_text")),
            "pass_fail_reasoning_available": bool(
                row.get("pass_fail_reasoning") or row.get("reasoning") or row.get("evaluation_reason")
            ),
        },
    }


def _source_refs(row: dict) -> list[dict]:
    refs = row.get("source_refs")
    if not isinstance(refs, list):
        refs = row.get("expected_refs")
    if not isinstance(refs, list):
        refs = row.get("opened_refs")
    return [item for item in refs if isinstance(item, dict)] if isinstance(refs, list) else []


def _commands(row: dict) -> list[Any]:
    commands = row.get("commands")
    if not isinstance(commands, list):
        commands = row.get("command_trace")
    if not isinstance(commands, list):
        commands = row.get("tool_calls")
    return list(commands) if isinstance(commands, list) else []


def _source_ref(ref: dict, *, internal: bool, index: int) -> dict:
    path = str(ref.get("path") or ref.get("source_path") or ref.get("file") or "")
    line = _int_or_none(ref.get("line") or ref.get("start_line"))
    end_line = _int_or_none(ref.get("end_line")) or line
    snippet = str(ref.get("snippet") or ref.get("text") or "") if internal else ""
    if internal:
        target = _format_target(path, line, end_line)
        return {
            "target": target,
            "path": path,
            "line": line,
            "end_line": end_line,
            "snippet": snippet,
            "raw_path_included": bool(path),
            "exact_line_numbers_included": line is not None,
        }
    return {
        "target": f"local_ref_{index:03d}",
        "path": "<local_path_redacted>" if path else "",
        "line_bucket": _line_bucket(line),
        "snippet": "<redacted>" if snippet else "",
        "raw_path_included": False,
        "exact_line_numbers_included": False,
    }


def _line_range(ref: dict, *, internal: bool) -> dict:
    line = _int_or_none(ref.get("line") or ref.get("start_line"))
    end_line = _int_or_none(ref.get("end_line")) or line
    if internal:
        return {"start_line": line, "end_line": end_line, "exact": line is not None}
    return {"bucket": _line_bucket(line), "exact": False}


def _command_family(command: Any, *, internal: bool) -> dict:
    if isinstance(command, dict):
        raw = str(command.get("cmd") or command.get("command") or command.get("tool") or command)
        family = str(command.get("family") or _family_from_command(raw))
        status = str(command.get("status") or command.get("result") or "")
    else:
        raw = str(command)
        family = _family_from_command(raw)
        status = ""
    return {
        "family": family,
        "command": raw if internal else "<command redacted>",
        "status": status if internal else ("present" if status else ""),
        "raw_command_included": internal,
    }


def _family_from_command(command: str) -> str:
    lowered = command.strip().lower()
    if not lowered:
        return "unknown"
    if "fielded-bm25" in lowered:
        return "fielded-bm25"
    if "rrf" in lowered:
        return "rrf-fusion"
    if "semantic-debug" in lowered:
        return "semantic-debug"
    if "read-more" in lowered:
        return "read-more"
    if "rg " in f" {lowered}" or lowered.startswith("rg"):
        return "raw-rg"
    return lowered.split()[0]


def _summary(rows: list[dict]) -> dict:
    return {
        "row_count": len(rows),
        "rows_with_source_refs": sum(1 for row in rows if row["diagnosis_inputs"]["source_ref_count"]),
        "rows_with_commands": sum(1 for row in rows if row["diagnosis_inputs"]["command_count"]),
        "rows_with_final_answer": sum(1 for row in rows if row["diagnosis_inputs"]["final_answer_available"]),
        "rows_with_pass_fail_reasoning": sum(1 for row in rows if row["diagnosis_inputs"]["pass_fail_reasoning_available"]),
    }


def _privacy_contract(*, internal: bool) -> dict[str, bool]:
    if internal:
        return {
            "raw_query_text_included": True,
            "raw_private_paths_included": True,
            "exact_line_numbers_included": True,
            "raw_source_refs_included": True,
            "raw_command_text_included": True,
            "final_answer_text_included": True,
            "pass_fail_reasoning_included": True,
            "committed_safe_without_sanitization": False,
            "external_upload_performed": False,
        }
    return {
        "raw_query_text_included": False,
        "raw_private_paths_included": False,
        "exact_line_numbers_included": False,
        "raw_source_refs_included": False,
        "raw_command_text_included": False,
        "final_answer_text_included": False,
        "pass_fail_reasoning_included": False,
        "committed_safe_without_sanitization": True,
        "external_upload_performed": False,
    }


def _raw_or_redacted(value: Any, internal: bool) -> str:
    if value is None:
        return ""
    return str(value) if internal else "<redacted>"


def _public_id(value: Any) -> str:
    return "present" if value not in (None, "") else "unknown"


def _format_target(path: str, line: int | None, end_line: int | None) -> str:
    if not path:
        return ""
    if line is None:
        return path
    if end_line and end_line != line:
        return f"{path}:{line}-{end_line}"
    return f"{path}:{line}"


def _line_bucket(line: int | None) -> str:
    if line is None:
        return "unknown"
    if line <= 128:
        return "1-128"
    if line <= 512:
        return "129-512"
    if line <= 1024:
        return "513-1024"
    return "1025+"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
