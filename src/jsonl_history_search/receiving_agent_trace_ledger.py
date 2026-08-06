from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_REPORT_DIR = Path("codex/state/jsh-1513m4-receiving-agent-proof-harness-and-review-ledger")
DEFAULT_REPORT_JSON = DEFAULT_REPORT_DIR / "receiving-agent-trace-ledger.json"
DEFAULT_REPORT_MD = DEFAULT_REPORT_DIR / "receiving-agent-trace-ledger.md"

JSONL_CLI_RE = re.compile(r"(?:^|[\s;&|('\"`])jsonl-history-search(?:[\s'\"`]|$)")
JSONL_PACKAGE_RE = re.compile(
    r"(?:^|[\s;&|('\"`])(?:"
    r"import\s+jsonl_history_search(?:\b|\.)|"
    r"from\s+jsonl_history_search(?:\.[A-Za-z_][\w.]*)?\s+import\b|"
    r"(?:\S*[/\\])?(?:python(?:3(?:\.\d+)?)?|py)\s+-m\s+jsonl_history_search(?:\b|\.)"
    r")"
)
JSONL_SKILL_RE = re.compile(r"(?:^|[/\\])skills[/\\]jsonl-history-search(?:[/\\]|$)")
CONVO_ALIAS_RE = re.compile(r"(?:^|[\s;&|('\"`])convo(?:[\s'\"`]|$)")
READ_MORE_RE = re.compile(r"(?:^|[\s;&|('\"`])jsonl-history-search\s+read-more(?:[\s'\"`]|$)")
SEMANTIC_DEBUG_RE = re.compile(r"(?:^|[\s;&|('\"`])jsonl-history-search\s+semantic-debug(?:[\s'\"`]|$)")
ASK_EXECUTE_RE = re.compile(r"(?:^|[\s;&|('\"`])jsonl-history-search\s+ask\s+--execute(?:[\s'\"`]|$)")
SKILL_CHECK_RE = re.compile(r"(?:^|[\s;&|('\"`])jsonl-history-search\s+skill-(?:check|audit)(?:[\s'\"`]|$)")
BROAD_SEARCH_PATTERNS = (
    re.compile(r"(?:^|[\s;&|('\"`])rg(?:[\s'\"`]|$)"),
    re.compile(r"(?:^|[\s;&|('\"`])grep(?:[\s'\"`]|$)"),
    re.compile(r"(?:^|[\s;&|('\"`])git\s+(?:grep|log|show)(?:[\s'\"`]|$)"),
    re.compile(r"(?:^|[\s;&|('\"`])find\s+(?:[./~]|['\"][./~])"),
    re.compile(r"(?:^|[\s;&|('\"`])ls\s+-[A-Za-z]*R[A-Za-z]*(?:[\s'\"`]|$)"),
)
ROUTE_FAMILY_RE = re.compile(r"(?im)^\s*Route family:\s*([^\n\r]+?)\s*$")
CHOSEN_ROUTE_RE = re.compile(r"(?im)^\s*Chosen route:\s*([^\n\r]+?)\s*$")
JSON_ROUTE_FAMILY_RE = re.compile(r'"route_family"\s*:\s*"([^"]+)"')
SOURCE_REF_RE = re.compile(r"\b(?:jsonl_source_ref|source ref|source-ref|raw/session|primary)\b", re.IGNORECASE)
CAP_LANGUAGE_RE = re.compile(r"\b(?:cap|coverage|bounded|unproved|not proved|cannot prove|uncertain|uncertainty)\b", re.IGNORECASE)
PRIVATE_PATH_TOKEN_RE = re.compile(r"(?:(?:^|[/\\])\.private[/\\])")
ABSOLUTE_PRIVATE_RE = re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\\\Users\\\\)")
RUNTIME_SECRET_RE = re.compile(
    r"(?:auth\.json|installation_id|models_cache\.json|state_\d+\.sqlite|goals_\d+\.sqlite|logs_\d+\.sqlite|[/\\]sessions[/\\])",
    re.IGNORECASE,
)
SESSION_ID_RE = re.compile(r"\b(?:thread_id|session_id|installation_id)\b", re.IGNORECASE)


@dataclass(frozen=True)
class LaneSpec:
    lane_id: str
    path: Path


@dataclass(frozen=True)
class ParsedEvents:
    events: list[dict[str, Any]]
    parse_errors: int
    event_file_bytes: int


def parse_lf_delimited_json(path: Path) -> ParsedEvents:
    """Parse Codex --json output as one JSON object per LF byte.

    Python's ``splitlines()`` treats U+2028 and similar separators as record
    breaks. Codex JSONL uses LF delimiters, so embedded Unicode separators must
    remain inside the JSON string.
    """
    if not path.exists():
        return ParsedEvents(events=[], parse_errors=0, event_file_bytes=0)
    raw = path.read_text(encoding="utf-8", errors="replace")
    events: list[dict[str, Any]] = []
    parse_errors = 0
    for record in raw.split("\n"):
        if not record.strip():
            continue
        try:
            event = json.loads(record)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            parse_errors += 1
    return ParsedEvents(events=events, parse_errors=parse_errors, event_file_bytes=path.stat().st_size)


def _sha256_12_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _sha256_12_text(text: str) -> str:
    return _sha256_12_bytes(text.encode("utf-8", errors="replace"))


def _artifact_digest(path: Path) -> dict[str, Any]:
    present = path.exists()
    payload: dict[str, Any] = {
        "present": present,
        "bytes": path.stat().st_size if present else 0,
        "sha256_12": _sha256_12_bytes(path.read_bytes()) if present else None,
    }
    return payload


def _safe_private_handle(path: Path, *, repo_root: Path | None = None) -> str:
    text = path.as_posix()
    marker = "/.private/"
    if marker in text:
        return ".private/" + text.split(marker, 1)[1]
    if text.startswith(".private/"):
        return text
    if repo_root is not None:
        try:
            rel = path.resolve().relative_to(repo_root.resolve())
            if rel.parts and rel.parts[0] == ".private":
                return rel.as_posix()
        except ValueError:
            pass
    return f"external_private_artifact:{_sha256_12_text(text)}"


def _lane_file(lane_path: Path, *names: str) -> Path:
    for name in names:
        candidate = lane_path / name
        if candidate.exists():
            return candidate
    return lane_path / names[0]


def _load_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _event_command(event: dict[str, Any]) -> str | None:
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "command_execution":
        return None
    command = item.get("command") or item.get("cmd") or item.get("formatted_command")
    return command if isinstance(command, str) else ""


def _event_command_output(event: dict[str, Any]) -> str:
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "command_execution":
        return ""
    parts: list[str] = []
    for key in ("aggregated_output", "output", "stdout", "stderr"):
        value = item.get(key)
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


def _command_kind(command: str) -> str:
    lowered = command.lower()
    if READ_MORE_RE.search(command):
        return "jsonl_read_more"
    if SEMANTIC_DEBUG_RE.search(command):
        return "jsonl_semantic_debug"
    if ASK_EXECUTE_RE.search(command):
        return "jsonl_ask_execute"
    if SKILL_CHECK_RE.search(command):
        return "jsonl_skill_check_or_audit"
    if JSONL_CLI_RE.search(command):
        return "jsonl_cli_other"
    if JSONL_PACKAGE_RE.search(command):
        return "jsonl_package_import_or_module"
    if CONVO_ALIAS_RE.search(command):
        return "convo_alias"
    if JSONL_SKILL_RE.search(command):
        return "jsonl_skill_file_read"
    if "git " in lowered:
        return "git"
    if any(pattern.search(command) for pattern in BROAD_SEARCH_PATTERNS):
        return "broad_search_or_listing"
    if "sed " in lowered or "cat " in lowered or "nl " in lowered:
        return "file_read"
    return "other_shell"


def _is_broad_search(command: str) -> bool:
    return any(pattern.search(command) for pattern in BROAD_SEARCH_PATTERNS)


def _wrong_command_reasons(command: str, *, expected_route_family: str | None) -> list[str]:
    reasons: list[str] = []
    if JSONL_CLI_RE.search(command) and " find " in command and "--max-results" in command:
        reasons.append("find_max_results_shape")
    if READ_MORE_RE.search(command) and "--context" in command:
        reasons.append("read_more_context_shape")
    if READ_MORE_RE.search(command) and "--lines" in command:
        reasons.append("read_more_lines_shape")
    if expected_route_family and expected_route_family != "skill_check" and SKILL_CHECK_RE.search(command):
        reasons.append("skill_check_selected_for_non_skill_route")
    return reasons


def _expected_jsonl_activation(lane_id: str, metadata: dict[str, Any]) -> bool:
    family = str(metadata.get("family") or "").lower()
    lowered_lane = lane_id.lower()
    negative_markers = ("negative", "local_status", "local-status", "no_jsonl", "no-jsonl")
    return not (any(marker in lowered_lane for marker in negative_markers) or any(marker in family for marker in negative_markers))


def _route_family_from_metadata(lane_id: str, metadata: dict[str, Any]) -> str | None:
    family = metadata.get("family")
    if isinstance(family, str) and family.strip():
        return family.strip()
    lowered = lane_id.lower()
    if "action_proof" in lowered or "action-proof" in lowered:
        return "action_proof"
    if "old_context" in lowered or "compaction" in lowered:
        return "long_range_history"
    if "local_status" in lowered:
        return "local_status_negative"
    return None


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _final_status(final_path: Path, final_text: str) -> dict[str, Any]:
    present = final_path.exists() and final_path.stat().st_size > 0
    return {
        "status": "present" if present else "missing",
        "bytes": final_path.stat().st_size if final_path.exists() else 0,
        "sha256_12": _sha256_12_bytes(final_path.read_bytes()) if final_path.exists() else None,
        "source_grounding_language_present": bool(SOURCE_REF_RE.search(final_text)),
        "cap_or_coverage_language_present": bool(CAP_LANGUAGE_RE.search(final_text)),
        "final_correctness": "not_adjudicated_by_ledger",
        "claim_boundary": "ledger_reports_presence_and_signals_only_not_answer_correctness",
    }


def classify_lane(lane_id: str, lane_path: Path, *, repo_root: Path | None = None) -> dict[str, Any]:
    lane_path = lane_path.expanduser()
    if lane_path.is_file():
        lane_path = lane_path.parent
    events_path = _lane_file(lane_path, "events.jsonl")
    final_path = _lane_file(lane_path, "final-answer.txt", "final_answer.txt")
    prompt_path = _lane_file(lane_path, "prompt.txt")
    stderr_path = _lane_file(lane_path, "stderr.txt", "codex_stderr.txt")
    metadata_path = _lane_file(lane_path, "metadata.json", "runner_metadata.json")

    metadata = _load_metadata(metadata_path)
    expected_route_family = _route_family_from_metadata(lane_id, metadata)
    expected_activation = _expected_jsonl_activation(lane_id, metadata)
    parsed = parse_lf_delimited_json(events_path)
    final_text = final_path.read_text(encoding="utf-8", errors="replace") if final_path.exists() else ""

    event_counts: dict[str, int] = {}
    chronology: list[dict[str, Any]] = []
    completed_order: list[dict[str, Any]] = []
    completed_commands: list[str] = []
    command_started = 0
    command_completed = 0
    surface_counts = {
        "jsonl_cli": 0,
        "jsonl_package_import": 0,
        "jsonl_skill_file": 0,
        "convo_alias": 0,
    }
    command_kind_counts: dict[str, int] = {}
    route_families: list[str] = []
    chosen_routes: list[str] = []
    wrong_reasons: list[str] = []
    read_more_mentions = 0
    semantic_debug_mentions = 0
    source_ref_signal_count = 0

    for event_index, event in enumerate(parsed.events):
        event_type = str(event.get("type") or "<missing>")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        raw_probe = json.dumps(event, ensure_ascii=False, sort_keys=True)
        lowered_probe = raw_probe.lower()
        read_more_mentions += lowered_probe.count("read-more")
        semantic_debug_mentions += lowered_probe.count("semantic-debug")
        source_ref_signal_count += len(SOURCE_REF_RE.findall(raw_probe))

        command = _event_command(event)
        if command is None:
            continue
        command_kind = _command_kind(command)
        command_kind_counts[command_kind] = command_kind_counts.get(command_kind, 0) + 1
        if event_type == "item.started":
            command_started += 1
        if event_type == "item.completed":
            command_completed += 1
            completed_commands.append(command)
        if JSONL_CLI_RE.search(command):
            surface_counts["jsonl_cli"] += 1
        if JSONL_PACKAGE_RE.search(command):
            surface_counts["jsonl_package_import"] += 1
        if JSONL_SKILL_RE.search(command):
            surface_counts["jsonl_skill_file"] += 1
        if CONVO_ALIAS_RE.search(command):
            surface_counts["convo_alias"] += 1
        if event_type == "item.completed":
            for reason in _wrong_command_reasons(command, expected_route_family=expected_route_family):
                wrong_reasons.append(reason)

        command_entry = {
            "event_index": event_index,
            "lifecycle_event": event_type,
            "command_kind": command_kind,
            "command_hash_12": _sha256_12_text(command),
            "jsonl_product_surface": command_kind
            in {"jsonl_read_more", "jsonl_semantic_debug", "jsonl_ask_execute", "jsonl_cli_other", "jsonl_package_import_or_module", "convo_alias"},
            "broad_search_or_listing": _is_broad_search(command),
        }
        chronology.append(command_entry)
        if event_type == "item.completed":
            completed_order.append(
                {
                    "completed_index": len(completed_order),
                    "command_kind": command_kind,
                    "command_hash_12": command_entry["command_hash_12"],
                    "jsonl_product_surface": command_entry["jsonl_product_surface"],
                    "broad_search_or_listing": command_entry["broad_search_or_listing"],
                }
            )

        output_text = _event_command_output(event)
        if output_text:
            route_families.extend(ROUTE_FAMILY_RE.findall(output_text))
            route_families.extend(JSON_ROUTE_FAMILY_RE.findall(output_text))
            chosen_routes.extend(CHOSEN_ROUTE_RE.findall(output_text))
            source_ref_signal_count += len(SOURCE_REF_RE.findall(output_text))

    product_surface_count = (
        surface_counts["jsonl_cli"] + surface_counts["jsonl_package_import"] + surface_counts["convo_alias"]
    )
    skill_only_mentions = surface_counts["jsonl_skill_file"] > 0 and product_surface_count == 0
    activated = product_surface_count > 0
    read_more_completed = sum(1 for command in completed_commands if READ_MORE_RE.search(command))
    semantic_debug_completed = sum(1 for command in completed_commands if SEMANTIC_DEBUG_RE.search(command))
    ask_execute_completed = sum(1 for command in completed_commands if ASK_EXECUTE_RE.search(command))
    skill_check_completed = sum(1 for command in completed_commands if SKILL_CHECK_RE.search(command))
    broad_search_completed = sum(1 for command in completed_commands if _is_broad_search(command))
    first_product = next((entry for entry in completed_order if entry["jsonl_product_surface"]), None)

    if expected_activation:
        overtrigger_status = "not_negative_control"
    elif activated:
        overtrigger_status = "negative_control_failed_jsonl_activated"
    else:
        overtrigger_status = "negative_control_passed_no_jsonl_activation"

    if not expected_activation:
        activation_status = "not_expected_for_negative_control"
    elif activated:
        activation_status = "activated"
    elif skill_only_mentions:
        activation_status = "skill_only_no_product_activation"
    else:
        activation_status = "missing_expected_jsonl_activation"

    if read_more_completed:
        follow_through_status = "read_more_used"
    elif activated:
        follow_through_status = "jsonl_activated_without_read_more"
    else:
        follow_through_status = "not_applicable_no_jsonl_activation"

    wrong_reason_counts = {reason: wrong_reasons.count(reason) for reason in sorted(set(wrong_reasons))}
    lane_report = {
        "lane_id": lane_id,
        "private_artifacts": {
            "events": {"handle": _safe_private_handle(events_path, repo_root=repo_root), **_artifact_digest(events_path)},
            "prompt": {"handle": _safe_private_handle(prompt_path, repo_root=repo_root), **_artifact_digest(prompt_path)},
            "final": {"handle": _safe_private_handle(final_path, repo_root=repo_root), **_artifact_digest(final_path)},
            "stderr": {"handle": _safe_private_handle(stderr_path, repo_root=repo_root), **_artifact_digest(stderr_path)},
            "metadata": {"handle": _safe_private_handle(metadata_path, repo_root=repo_root), **_artifact_digest(metadata_path)},
        },
        "metadata": {
            "model": metadata.get("model", "unknown"),
            "return_code": metadata.get("return_code", "unknown"),
            "timed_out": metadata.get("timed_out", "unknown"),
            "elapsed_seconds": metadata.get("elapsed_seconds", "unknown"),
            "expected_route_family": expected_route_family,
            "expected_jsonl_activation": expected_activation,
        },
        "event_parse": {
            "event_file_present": events_path.exists(),
            "event_file_bytes": parsed.event_file_bytes,
            "event_count": len(parsed.events),
            "parse_errors": parsed.parse_errors,
            "event_counts": dict(sorted(event_counts.items())),
            "turn_completed_count": int(event_counts.get("turn.completed") or 0),
        },
        "activation": {
            "status": activation_status,
            "jsonl_product_surface_count": product_surface_count,
            "skill_file_mention_count": surface_counts["jsonl_skill_file"],
            "surface_counts": surface_counts,
            "first_product_command": first_product,
        },
        "command_chronology": chronology,
        "command_order": completed_order,
        "command_counts": {
            "started": command_started,
            "completed": command_completed,
            "completed_unique_command_hashes": len({entry["command_hash_12"] for entry in completed_order}),
            "jsonl_completed": sum(1 for command in completed_commands if JSONL_CLI_RE.search(command) or JSONL_PACKAGE_RE.search(command) or CONVO_ALIAS_RE.search(command)),
            "read_more_completed": read_more_completed,
            "semantic_debug_completed": semantic_debug_completed,
            "ask_execute_completed": ask_execute_completed,
            "skill_check_completed": skill_check_completed,
            "broad_search_completed": broad_search_completed,
            "wrong_command_completed": len(wrong_reasons),
            "wrong_or_broad_completed": len(wrong_reasons) + broad_search_completed,
            "read_more_mentions": read_more_mentions,
            "semantic_debug_mentions": semantic_debug_mentions,
        },
        "command_kind_counts": dict(sorted(command_kind_counts.items())),
        "command_shape_relearning": {
            "status": "wrong_or_relearned_command_shape_seen" if wrong_reasons else "no_wrong_command_shape_seen",
            "wrong_command_reason_counts": wrong_reason_counts,
        },
        "route_selection": {
            "expected_route_family": expected_route_family,
            "route_families_detected": _dedupe_preserve_order(route_families),
            "chosen_routes_detected": _dedupe_preserve_order(chosen_routes),
            "route_family_detected": bool(route_families or chosen_routes),
            "method_selection_packet_seen": "method_selection_packet" in "\n".join(_event_command_output(event) for event in parsed.events).lower()
            or "method selection packet" in "\n".join(_event_command_output(event) for event in parsed.events).lower(),
        },
        "source_follow_through": {
            "status": follow_through_status,
            "read_more_completed": read_more_completed,
            "primary_ref_use_signal_count": source_ref_signal_count,
            "primary_ref_use": source_ref_signal_count > 0 or read_more_completed > 0,
        },
        "final_answer": _final_status(final_path, final_text),
        "overtrigger": {
            "status": overtrigger_status,
            "expected_jsonl_activation": expected_activation,
            "jsonl_activated": activated,
        },
        "claim_boundaries": {
            "raw_trace_text_committed": False,
            "final_answer_correctness_claimed": False,
            "release_readiness_claimed": False,
            "token_savings_claimed": False,
        },
    }
    reject_unsafe_report_payload({"receiving_agent_trace_ledger": {"lanes": [lane_report]}})
    return lane_report


def build_receiving_agent_trace_ledger(lanes: Iterable[LaneSpec], *, repo_root: Path | None = None) -> dict[str, Any]:
    lane_reports = [classify_lane(lane.lane_id, lane.path, repo_root=repo_root) for lane in lanes]
    summary = {
        "lane_count": len(lane_reports),
        "activated_count": sum(1 for lane in lane_reports if lane["activation"]["status"] == "activated"),
        "negative_control_pass_count": sum(
            1 for lane in lane_reports if lane["overtrigger"]["status"] == "negative_control_passed_no_jsonl_activation"
        ),
        "parse_error_lane_count": sum(1 for lane in lane_reports if lane["event_parse"]["parse_errors"]),
        "completed_command_count": sum(int(lane["command_counts"]["completed"]) for lane in lane_reports),
        "read_more_completed_count": sum(int(lane["command_counts"]["read_more_completed"]) for lane in lane_reports),
        "broad_search_completed_count": sum(int(lane["command_counts"]["broad_search_completed"]) for lane in lane_reports),
        "wrong_command_completed_count": sum(int(lane["command_counts"]["wrong_command_completed"]) for lane in lane_reports),
    }
    payload = {
        "receiving_agent_trace_ledger": {
            "schema_version": "1.0",
            "privacy_boundary": (
                "committed-safe: raw prompts, final prose, stderr, command arguments, "
                "absolute paths, thread/session ids, and runtime/auth paths are omitted"
            ),
            "summary": summary,
            "lanes": lane_reports,
            "claim_boundaries": {
                "offline_replay_only": True,
                "live_model_send_run": False,
                "benchmark_suite_claimed": False,
                "final_answer_quality_claim_supported": False,
            },
        }
    }
    reject_unsafe_report_payload(payload)
    return payload


def write_receiving_agent_trace_ledger(
    report_output: Path,
    lanes: Iterable[LaneSpec],
    *,
    repo_root: Path | None = None,
    output_format: str = "json",
) -> dict[str, Any]:
    payload = build_receiving_agent_trace_ledger(lanes, repo_root=repo_root)
    report_output.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "markdown":
        report_output.write_text(render_receiving_agent_trace_ledger_markdown(payload), encoding="utf-8")
    else:
        report_output.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def render_receiving_agent_trace_ledger_markdown(payload: dict[str, Any]) -> str:
    ledger = payload["receiving_agent_trace_ledger"]
    summary = ledger["summary"]
    lines = [
        "# Receiving-Agent Trace Ledger",
        "",
        f"- Schema: `{ledger['schema_version']}`",
        f"- Privacy boundary: {ledger['privacy_boundary']}",
        f"- Lanes: `{summary['lane_count']}`",
        f"- Activated lanes: `{summary['activated_count']}`",
        f"- Negative controls passed: `{summary['negative_control_pass_count']}`",
        f"- Completed commands: `{summary['completed_command_count']}`",
        f"- Read-more commands: `{summary['read_more_completed_count']}`",
        f"- Broad searches/listings: `{summary['broad_search_completed_count']}`",
        f"- Wrong command shapes/routes: `{summary['wrong_command_completed_count']}`",
        "",
    ]
    for lane in ledger["lanes"]:
        counts = lane["command_counts"]
        route = lane["route_selection"]
        final = lane["final_answer"]
        lines.extend(
            [
                f"## {lane['lane_id']}",
                "",
                f"- Activation: `{lane['activation']['status']}`",
                f"- Overtrigger: `{lane['overtrigger']['status']}`",
                f"- Expected route family: `{lane['metadata']['expected_route_family']}`",
                f"- Detected route families: `{', '.join(route['route_families_detected']) or '(none)'}`",
                f"- Chosen routes: `{', '.join(route['chosen_routes_detected']) or '(none)'}`",
                f"- Method packet seen: `{route['method_selection_packet_seen']}`",
                f"- Commands started/completed: `{counts['started']}` / `{counts['completed']}`",
                f"- JSONL/read-more/semantic-debug/ask-execute: `{counts['jsonl_completed']}` / `{counts['read_more_completed']}` / `{counts['semantic_debug_completed']}` / `{counts['ask_execute_completed']}`",
                f"- Broad/wrong/wrong-or-broad: `{counts['broad_search_completed']}` / `{counts['wrong_command_completed']}` / `{counts['wrong_or_broad_completed']}`",
                f"- Read-more mentions vs commands: `{counts['read_more_mentions']}` / `{counts['read_more_completed']}`",
                f"- Source follow-through: `{lane['source_follow_through']['status']}`; primary-ref signal `{lane['source_follow_through']['primary_ref_use']}`",
                f"- Final answer: `{final['status']}`, bytes `{final['bytes']}`, correctness `{final['final_correctness']}`",
                f"- Cap/coverage language: `{final['cap_or_coverage_language_present']}`; source-grounding language: `{final['source_grounding_language_present']}`",
                "- Private handles:",
            ]
        )
        for artifact_name, artifact in lane["private_artifacts"].items():
            lines.append(
                f"  - `{artifact_name}`: `{artifact['handle']}` bytes `{artifact['bytes']}` sha `{artifact['sha256_12']}`"
            )
        lines.append("- Command order:")
        for entry in lane["command_order"]:
            lines.append(
                f"  - `{entry['completed_index']}` `{entry['command_kind']}` hash `{entry['command_hash_12']}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def reject_unsafe_report_payload(payload: Any) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    forbidden_values = {
        "absolute_home_path": ABSOLUTE_PRIVATE_RE,
        "runtime_secret_path": RUNTIME_SECRET_RE,
        "session_or_thread_identifier_field": SESSION_ID_RE,
    }
    for label, pattern in forbidden_values.items():
        if pattern.search(serialized):
            raise ValueError(f"receiving-agent ledger privacy rejection: {label}")
    forbidden_keys = {
        "raw_command",
        "command",
        "aggregated_output",
        "stdout",
        "stderr_text",
        "prompt_text",
        "final_answer_text",
        "raw_prompt",
        "raw_final",
        "thread_id",
        "session_id",
    }
    _reject_forbidden_keys(payload, forbidden_keys)


def _reject_forbidden_keys(value: Any, forbidden_keys: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in forbidden_keys:
                raise ValueError(f"receiving-agent ledger privacy rejection: forbidden key {key}")
            _reject_forbidden_keys(child, forbidden_keys)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_keys(child, forbidden_keys)


def parse_lane_arg(value: str) -> LaneSpec:
    if "=" not in value:
        raise ValueError("lane must use id=path")
    lane_id, raw_path = value.split("=", 1)
    lane_id = lane_id.strip()
    raw_path = raw_path.strip()
    if not lane_id or not raw_path:
        raise ValueError("lane must use non-empty id=path")
    return LaneSpec(lane_id=lane_id, path=Path(raw_path))
