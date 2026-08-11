from __future__ import annotations

import hashlib
import json
import re
import shlex
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
CONVO_SKILL_RE = re.compile(r"(?:^|[/\\])skills[/\\]convo(?:[/\\]|$)")
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
ANSWER_READY_RE = re.compile(r"(?im)(?:^|\n)\s*ANSWER READY\b")
NOT_ANSWERABLE_RE = re.compile(r"(?im)(?:^|\n)\s*NOT ANSWERABLE YET\b")
STALE_SELECTED_ANCHOR_RE = re.compile(r"\bstale_selected_anchor\b", re.IGNORECASE)
SELECTED_ANCHOR_FRESHNESS_RE = re.compile(r"\bselected_anchor_freshness\b", re.IGNORECASE)
PARENT_MAIN_ANCHOR_FRESHNESS_RE = re.compile(r"\bparent_main_anchor_freshness\b", re.IGNORECASE)
AUDIT_CONTINUATION_WORKFLOW_RE = re.compile(r"\bAUDIT CONTINUATION WORKFLOW\b|\baudit_continuation_workflow\b", re.IGNORECASE)
PARTIAL_PACKET_RE = re.compile(
    r"\b(?:cap[_ -]?hit|partial[_ -]?packet)\b|\bNOT ANSWERABLE YET\b|"
    r"\bAUDIT CONTINUATION WORKFLOW\b|\baudit_continuation_workflow\b",
    re.IGNORECASE,
)
TARGET_PERSISTENCE_RE = re.compile(r"\btarget[_ -]persistence\b", re.IGNORECASE)
TARGET_DRIFT_RE = re.compile(r"\btarget[_ -]drift\b|\bdrift_risk_reads\b|\btarget_drift_risk\b", re.IGNORECASE)
TARGET_MISMATCH_RE = re.compile(
    r"\b(?:status|coverage_label|target mismatch)\s*[:=]\s*[`'\"]?target_mismatch_detected\b",
    re.IGNORECASE,
)
PRIVATE_PATH_TOKEN_RE = re.compile(r"(?:(?:^|[/\\])\.private[/\\])")
ABSOLUTE_PRIVATE_RE = re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\\\Users\\\\)")
RUNTIME_SECRET_RE = re.compile(
    r"(?:auth\.json|installation_id|models_cache\.json|state_\d+\.sqlite|goals_\d+\.sqlite|logs_\d+\.sqlite|[/\\]sessions[/\\])",
    re.IGNORECASE,
)
SESSION_ID_RE = re.compile(r"\b(?:thread_id|session_id|installation_id)\b", re.IGNORECASE)
PROTECTED_EVALUATION_ARTIFACT_RE = re.compile(
    r"(?ix)(?<![!A-Za-z0-9_.-])(?:"
    r"(?:benchmarks?|reports?|evals?|evaluators?)[/\\][^\s'\"`;&|]+|"
    r"codex[/\\]state[/\\]reviews[/\\][^\s'\"`;&|]+|"
    r"(?<![^\s'\"`;&|/\\])[^\s'\"`;&|/\\]*(?:gold|answer[-_]?key|evaluator|adjudicat(?:or|ion)|"
    r"source[-_]?map|case[-_]?packet|expected[-_]?facets?)[^\s'\"`;&|/\\]*\.(?:json|jsonl|md|txt|yaml|yml)"
    r")"
)
ANSWER_KEY_ARTIFACT_RE = re.compile(
    r"(?ix)(?<![^\s'\"`;&|/\\])[^\s'\"`;&|/\\]*(?:gold|answer[-_]?key|evaluator|adjudicat(?:or|ion)|"
    r"source[-_]?map|case[-_]?packet|expected[-_]?facets?)[^\s'\"`;&|/\\]*\."
    r"(?:json|jsonl|md|txt|yaml|yml)"
)
BENCHMARK_EVALUATION_PATH_RE = re.compile(
    r"(?ix)(?<![A-Za-z0-9_.-])(?:benchmarks?|evals?|evaluators?)[/\\][^\s'\"`;&|]+"
)
CURRENT_LOOP_LANE_ARTIFACT_RE = re.compile(
    r"(?ix)(?<![^\s'\"`;&|])[^\s'\"`;&|]*(?:lanes|evals?|evaluators?)[/\\][^\s'\"`;&|]+[/\\]"
    r"(?:prompt|final[-_]?answer|events|metadata|runner[-_]?metadata|command)\."
    r"(?:json|jsonl|md|txt|yaml|yml)"
)
SECONDARY_CONTEXT_PATH_RE = re.compile(
    r"(?ix)(?:^|[\s'\"`])(?:[^\s'\"`;&|]+[/\\])?"
    r"(?:tasks|docs|reports|scripts|src|tests|skills|reviews|codex[/\\]state)[/\\]"
    r"[^\s'\"`;&|]+"
)
UNCLEAR_SOURCE_PATH_RE = re.compile(
    r"(?ix)[^\s'\"`;&|]+\.(?:json|jsonl|md|txt|yaml|yml)(?::\d+(?:-\d+)?)?"
)
CURRENT_LOOP_ID_RE = re.compile(r"(?i)\bjsh[-_]?([0-9]+)")
RAW_WINDOW_RECORD_LABEL_RE = re.compile(
    r"(?im)^-\s+(?P<path>[^\n]+\.jsonl):(?P<line>\d+):\s+"
    r"(?P<role>[a-z_][a-z0-9_]*)\s*$"
)
RAW_WINDOW_RECORD_COUNT_RE = re.compile(r"(?im)^Records:\s+(\d+)\s+included\b")
RAW_WINDOW_LINE_RANGE_RE = re.compile(
    r"(?im)^Lines:\s+(\d+)\.\.(\d+)\s+\(anchor\s+\d+\)\s*$"
)
RAW_SETUP_ONLY_RECORD_LABELS = {
    "developer",
    "session_meta",
    "task_started",
    "turn_context",
}
RAW_HISTORY_PATH_RE = re.compile(
    r"(?ix)(?:"
    r"(?:^|[/\\])(?:sessions|archived_sessions)[/\\][^\s'\"`;&|]+\.jsonl(?:[:#]\d+)?|"
    r"(?:^|[/\\])(?:history|transcription-history)\.jsonl(?:[:#]\d+)?|"
    r"[^\s'\"`;&|/\\]+\.jsonl(?:[:#]\d+)?"
    r")"
)
FILE_ACCESS_RE = re.compile(
    r"(?:^|[\s;&|('\"`])(?:cat|sed|nl|head|tail|less|more|rg|grep|find|ls|jq|awk)(?:[\s'\"`]|$)|"
    r"\bopen\s*\(",
    re.IGNORECASE,
)
PRIMARY_CONTENT_ACCESS_RE = re.compile(
    r"(?:^|[\s;&|('\"`])(?:cat|sed|nl|head|tail|less|more|rg|grep|jq|awk)(?:[\s'\"`]|$)|"
    r"(?:^|[\s;&|('\"`])git\s+(?:grep|show)(?:[\s'\"`]|$)|"
    r"\bopen\s*\(",
    re.IGNORECASE,
)
NON_PRIMARY_JSONL_NAMES = {"events.jsonl", "session_index.jsonl"}
RG_OPTIONS_WITH_VALUE = {
    "-A",
    "--after-context",
    "-B",
    "--before-context",
    "-C",
    "--context",
    "-e",
    "--regexp",
    "-f",
    "--file",
    "-g",
    "--glob",
    "--iglob",
    "-m",
    "--max-count",
    "-t",
    "--type",
    "-T",
    "--type-not",
}
GIT_GLOBAL_OPTIONS_WITH_VALUE = {
    "-C",
    "-c",
    "--config-env",
    "--exec-path",
    "--git-dir",
    "--namespace",
    "--super-prefix",
    "--work-tree",
}
PROVENANCE_SOURCE_FAMILY_MARKERS = (
    "provenance",
    "source",
    "action_proof",
    "action-proof",
    "agent_action",
)


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
    if CONVO_SKILL_RE.search(command):
        return "convo_skill_file_read"
    if "git " in lowered:
        return "git"
    if any(pattern.search(command) for pattern in BROAD_SEARCH_PATTERNS):
        return "broad_search_or_listing"
    if "sed " in lowered or "cat " in lowered or "nl " in lowered:
        return "file_read"
    return "other_shell"


def _is_broad_search(command: str) -> bool:
    return any(pattern.search(command) for pattern in BROAD_SEARCH_PATTERNS)


def _is_provenance_source_family(expected_route_family: str | None) -> bool:
    family = (expected_route_family or "").lower()
    return any(marker in family for marker in PROVENANCE_SOURCE_FAMILY_MARKERS)


def _normalized_marker(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _current_loop_markers(lane_path: Path, metadata: dict[str, Any]) -> tuple[str, ...]:
    markers = {f"jsh{match}" for match in CURRENT_LOOP_ID_RE.findall(lane_path.as_posix())}
    configured = metadata.get("current_loop_markers")
    if isinstance(configured, list):
        for value in configured:
            if isinstance(value, str) and len(_normalized_marker(value)) >= 5:
                markers.add(_normalized_marker(value))
    return tuple(sorted(markers))


def _contains_current_loop_marker(text: str, markers: tuple[str, ...]) -> bool:
    normalized = _normalized_marker(text)
    return any(marker in normalized for marker in markers)


def _current_loop_answer_artifact_seen(text: str, markers: tuple[str, ...]) -> bool:
    for pattern in (PROTECTED_EVALUATION_ARTIFACT_RE, CURRENT_LOOP_LANE_ARTIFACT_RE):
        for match in pattern.finditer(text):
            if _contains_current_loop_marker(match.group(0), markers):
                return True
    return False


def _clearly_pre_existing_evaluation_artifacts_only(
    text: str,
    current_loop_markers: tuple[str, ...],
) -> bool:
    current_ids = {
        int(match)
        for marker in current_loop_markers
        for match in CURRENT_LOOP_ID_RE.findall(marker)
    }
    if not current_ids:
        return False

    artifact_seen = False
    current_id_floor = min(current_ids)
    for pattern in (
        PROTECTED_EVALUATION_ARTIFACT_RE,
        BENCHMARK_EVALUATION_PATH_RE,
        CURRENT_LOOP_LANE_ARTIFACT_RE,
    ):
        for artifact_match in pattern.finditer(text):
            artifact_seen = True
            artifact_ids = {
                int(match) for match in CURRENT_LOOP_ID_RE.findall(artifact_match.group(0))
            }
            if not artifact_ids or max(artifact_ids) >= current_id_floor:
                return False
    return artifact_seen


def _raw_window_contains_current_loop_answer_access(
    output: str,
    markers: tuple[str, ...],
) -> bool:
    record_matches = list(RAW_WINDOW_RECORD_LABEL_RE.finditer(output))
    if not record_matches:
        return False

    count_match = RAW_WINDOW_RECORD_COUNT_RE.search(output)
    trusted_matches = record_matches[:1]
    if count_match:
        included_count = int(count_match.group(1))
        range_match = RAW_WINDOW_LINE_RANGE_RE.search(output)
        if range_match and included_count == int(range_match.group(2)) - int(range_match.group(1)) + 1:
            source_path = record_matches[0].group("path")
            expected_lines = range(int(range_match.group(1)), int(range_match.group(2)) + 1)
            rendered_matches = []
            search_start = 0
            for expected_line in expected_lines:
                rendered_match = next(
                    (
                        match
                        for match in record_matches[search_start:]
                        if match.group("path") == source_path
                        and int(match.group("line")) == expected_line
                    ),
                    None,
                )
                if rendered_match is None:
                    rendered_matches = []
                    break
                rendered_matches.append(rendered_match)
                search_start = record_matches.index(rendered_match) + 1
            if len(rendered_matches) == included_count:
                trusted_matches = rendered_matches
        elif included_count == len(record_matches):
            trusted_matches = record_matches
        elif 1 < included_count < len(record_matches):
            # Legacy captures without line metadata preserve the first and trailing rendered headers.
            trusted_matches = [record_matches[0], *record_matches[-(included_count - 1) :]]

    for index, match in enumerate(trusted_matches):
        if match.group("role").lower() not in {"custom_tool_call", "custom_tool_call_output", "tool"}:
            continue
        block_end = trusted_matches[index + 1].start() if index + 1 < len(trusted_matches) else len(output)
        block = output[match.start() : block_end]
        answer_artifact_seen = bool(
            PROTECTED_EVALUATION_ARTIFACT_RE.search(block)
            or BENCHMARK_EVALUATION_PATH_RE.search(block)
            or CURRENT_LOOP_LANE_ARTIFACT_RE.search(block)
        )
        if (
            answer_artifact_seen
            and _contains_current_loop_marker(block, markers)
            and not _clearly_pre_existing_evaluation_artifacts_only(block, markers)
        ):
            return True
    return False


def _raw_window_has_ambiguous_record_boundaries(output: str) -> bool:
    record_matches = list(RAW_WINDOW_RECORD_LABEL_RE.finditer(output))
    count_match = RAW_WINDOW_RECORD_COUNT_RE.search(output)
    range_match = RAW_WINDOW_LINE_RANGE_RE.search(output)
    if not record_matches or not count_match or not range_match:
        return False

    included_count = int(count_match.group(1))
    range_start = int(range_match.group(1))
    range_end = int(range_match.group(2))
    if included_count != range_end - range_start + 1:
        return False

    source_path = record_matches[0].group("path")
    expected_lines = set(range(range_start, range_end + 1))
    seen: set[tuple[str, int]] = set()
    for match in record_matches:
        key = (match.group("path"), int(match.group("line")))
        if key[0] != source_path or key[1] not in expected_lines:
            continue
        if key in seen:
            return True
        seen.add(key)
    return False


def _has_primary_raw_history_path(text: str) -> bool:
    for match in RAW_HISTORY_PATH_RE.finditer(text):
        matched_path = match.group(0)
        if any(marker in matched_path for marker in "*?[]{}"):
            continue
        basename = matched_path.rstrip(":#0123456789").rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
        if basename not in NON_PRIMARY_JSONL_NAMES:
            return True
    return False


def _event_command_succeeded(event: dict[str, Any]) -> bool:
    item = event.get("item")
    if not isinstance(item, dict):
        return False
    exit_code = item.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        return exit_code == 0
    return str(item.get("status") or "").lower() == "completed"


def _raw_window_is_setup_only(output: str) -> bool:
    labels = [match.group("role") for match in RAW_WINDOW_RECORD_LABEL_RE.finditer(output)]
    return bool(labels) and all(label.lower() in RAW_SETUP_ONLY_RECORD_LABELS for label in labels)


def _rg_targets_are_primary_raw_history_only(command: str) -> bool:
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if not argv or argv[0] != "rg" or any(token in {";", "&&", "||", "|"} for token in argv):
        return False

    targets: list[str] = []
    pattern_supplied = False
    index = 1
    while index < len(argv):
        token = argv[index]
        if token == "--":
            remaining = argv[index + 1 :]
            if not pattern_supplied and remaining:
                pattern_supplied = True
                remaining = remaining[1:]
            targets.extend(remaining)
            break
        if token in RG_OPTIONS_WITH_VALUE:
            index += 1
            if token in {"-e", "--regexp", "-f", "--file"}:
                pattern_supplied = True
        elif token.startswith(("--regexp=", "--file=")) or (
            token.startswith(("-e", "-f")) and len(token) > 2
        ):
            pattern_supplied = True
        elif token.startswith("-"):
            pass
        elif not pattern_supplied:
            pattern_supplied = True
        else:
            targets.append(token)
        index += 1

    return bool(targets) and all(_has_primary_raw_history_path(target) for target in targets)


def _git_show_targets_primary_raw_history_only(command: str) -> bool:
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if (
        len(argv) >= 3
        and argv[0].rsplit("/", 1)[-1] in {"bash", "sh"}
        and argv[1] in {"-c", "-lc"}
    ):
        try:
            argv = shlex.split(argv[2])
        except ValueError:
            return False
    if not argv or argv[0].rsplit("/", 1)[-1] != "git":
        return False

    index = 1
    while index < len(argv) and argv[index] != "show":
        token = argv[index]
        if token in GIT_GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if token.startswith(("-C", "-c")) and token not in {"-C", "-c"}:
            index += 1
            continue
        if token.startswith(
            (
                "--config-env=",
                "--exec-path=",
                "--git-dir=",
                "--namespace=",
                "--super-prefix=",
                "--work-tree=",
            )
        ):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return False
    if index >= len(argv):
        return False

    targets = [token for token in argv[index + 1 :] if not token.startswith("-")]
    return (
        len(targets) == 1
        and ":" in targets[0]
        and _has_primary_raw_history_path(targets[0].split(":", 1)[1])
    )


def _evidence_access_classes(
    command: str,
    output: str,
    command_kind: str,
    *,
    command_succeeded: bool,
    current_loop_markers: tuple[str, ...],
) -> list[str]:
    read_capable = command_kind in {
        "file_read",
        "broad_search_or_listing",
        "git",
        "jsonl_read_more",
    } or bool(FILE_ACCESS_RE.search(command))
    if not read_capable:
        return []

    command_has_primary = _has_primary_raw_history_path(command)
    output_has_evidence = bool(output.strip())
    primary_probe = command
    git_show_targets_primary = _git_show_targets_primary_raw_history_only(command)
    output_discovery = (
        command_kind in {"broad_search_or_listing", "git"}
        and command_succeeded
        and output_has_evidence
        and not _rg_targets_are_primary_raw_history_only(command)
        and not git_show_targets_primary
    )
    if output_discovery:
        if not command_has_primary:
            primary_probe = f"{command}\n{output}"

    command_has_answer_artifact = (
        bool(BENCHMARK_EVALUATION_PATH_RE.search(command))
        or (not command_has_primary and bool(ANSWER_KEY_ARTIFACT_RE.search(command)))
    )
    command_pre_existing = _clearly_pre_existing_evaluation_artifacts_only(
        command,
        current_loop_markers,
    )
    command_current_loop = (
        command_has_answer_artifact and not command_pre_existing
    ) or _current_loop_answer_artifact_seen(command, current_loop_markers)
    output_pre_existing = _clearly_pre_existing_evaluation_artifacts_only(
        output,
        current_loop_markers,
    )
    output_current_loop = (
        output_discovery
        and not output_pre_existing
        and (
            bool(ANSWER_KEY_ARTIFACT_RE.search(output))
            or _current_loop_answer_artifact_seen(output, current_loop_markers)
        )
    ) or (
        command_kind == "jsonl_read_more"
        and (
            _raw_window_contains_current_loop_answer_access(output, current_loop_markers)
            or _raw_window_has_ambiguous_record_boundaries(output)
        )
    )
    current_loop_from_raw_output = (
        command_has_primary
        and not command_current_loop
        and not output_discovery
        and output_current_loop
    )

    classes: list[str] = []
    if command_current_loop or output_current_loop:
        classes.append("current_loop_evaluator_gold_report_review_material")
    primary_evidence_returned = (
        command_succeeded
        and output_has_evidence
        and not _raw_window_is_setup_only(output)
        and (
            command_kind == "jsonl_read_more"
            or git_show_targets_primary
            or (
                bool(PRIMARY_CONTENT_ACCESS_RE.search(command))
                and _has_primary_raw_history_path(primary_probe)
            )
        )
    )
    if primary_evidence_returned and not current_loop_from_raw_output:
        classes.append("primary_raw_evidence")

    if not classes and command_succeeded:
        if command_pre_existing or (output_discovery and output_pre_existing):
            classes.append("legitimate_pre_existing_secondary_context")
        elif SECONDARY_CONTEXT_PATH_RE.search(command):
            classes.append("legitimate_pre_existing_secondary_context")
        elif UNCLEAR_SOURCE_PATH_RE.search(command):
            classes.append("unclear_chronology")
    return classes


def _evidence_access_roles(classes: list[str]) -> list[str]:
    roles: list[str] = []
    if "current_loop_evaluator_gold_report_review_material" in classes:
        roles.append("protected_evaluation_artifact")
    if "primary_raw_evidence" in classes:
        roles.append("primary_raw_evidence")
    return roles


def _contamination_chronology(
    completed_order: list[dict[str, Any]],
    *,
    expected_route_family: str | None,
    positive_benchmark_lane: bool,
    parse_errors: int,
) -> dict[str, Any]:
    provenance_source_family = _is_provenance_source_family(expected_route_family)
    applies = positive_benchmark_lane
    primary_entries = [
        entry
        for entry in completed_order
        if "primary_raw_evidence" in entry["evidence_classes"]
    ]
    current_loop_entries = [
        entry
        for entry in completed_order
        if "current_loop_evaluator_gold_report_review_material" in entry["evidence_classes"]
    ]
    uncorrelated_evidence_count = sum(
        1 for entry in primary_entries + current_loop_entries if entry["started_index"] is None
    )
    primary_entries.sort(
        key=lambda entry: (
            entry["started_index"] is None,
            entry["started_index"] if entry["started_index"] is not None else entry["completed_index"],
        )
    )
    current_loop_entries.sort(
        key=lambda entry: (
            entry["started_index"] is None,
            entry["started_index"] if entry["started_index"] is not None else entry["completed_index"],
        )
    )
    first_primary_entry = primary_entries[0] if primary_entries else None
    first_current_loop_entry = current_loop_entries[0] if current_loop_entries else None
    first_primary_start = first_primary_entry["started_index"] if first_primary_entry else None
    first_current_loop_start = (
        first_current_loop_entry["started_index"] if first_current_loop_entry else None
    )
    current_loop_before_primary = first_current_loop_start is not None and (
        first_primary_start is None or first_current_loop_start <= first_primary_start
    )

    if not applies:
        route_cleanliness = "not_required"
        classification = "not_applicable_negative_control"
    elif parse_errors:
        route_cleanliness = "unadjudicable"
        classification = "unadjudicable_event_parse_errors"
    elif uncorrelated_evidence_count:
        route_cleanliness = "unadjudicable"
        classification = "unadjudicable_evidence_start_correlation"
    elif current_loop_before_primary:
        route_cleanliness = "fail"
        classification = (
            "current_loop_material_without_primary_raw_evidence"
            if first_primary_entry is None
            else "current_loop_material_before_or_with_first_primary_raw_evidence"
        )
    elif first_current_loop_entry is not None:
        route_cleanliness = "pass"
        classification = "current_loop_material_only_after_primary_raw_evidence"
    else:
        route_cleanliness = "pass"
        classification = "no_current_loop_material_access_before_primary_raw_evidence"

    evidence_class_counts = {
        evidence_class: sum(
            evidence_class in entry["evidence_classes"] for entry in completed_order
        )
        for evidence_class in (
            "primary_raw_evidence",
            "legitimate_pre_existing_secondary_context",
            "current_loop_evaluator_gold_report_review_material",
            "unclear_chronology",
        )
    }

    return {
        "applies_to_provenance_source_family": provenance_source_family,
        "applies_to_positive_benchmark_lane": applies,
        "route_cleanliness": route_cleanliness,
        "classification": classification,
        "first_primary_raw_evidence_started_index": first_primary_start,
        "first_current_loop_material_started_index": first_current_loop_start,
        "first_protected_artifact_started_index": first_current_loop_start,
        "first_primary_raw_evidence_completed_index": (
            first_primary_entry["completed_index"] if first_primary_entry else None
        ),
        "first_current_loop_material_completed_index": (
            first_current_loop_entry["completed_index"] if first_current_loop_entry else None
        ),
        "first_protected_artifact_completed_index": (
            first_current_loop_entry["completed_index"] if first_current_loop_entry else None
        ),
        "evidence_class_counts": evidence_class_counts,
        "primary_raw_evidence_access_count": len(primary_entries),
        "current_loop_material_access_count": len(current_loop_entries),
        "protected_artifact_access_count": len(current_loop_entries),
        "uncorrelated_evidence_access_count": uncorrelated_evidence_count,
        "current_loop_material_before_primary": current_loop_before_primary,
        "protected_artifact_before_primary": current_loop_before_primary,
    }


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
    if any(marker in lowered for marker in PROVENANCE_SOURCE_FAMILY_MARKERS):
        return lowered
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


def _latest_status_freshness_signals(texts: Iterable[str]) -> dict[str, Any]:
    joined = "\n".join(text for text in texts if text)
    stale_count = len(STALE_SELECTED_ANCHOR_RE.findall(joined))
    selected_count = len(SELECTED_ANCHOR_FRESHNESS_RE.findall(joined))
    parent_main_count = len(PARENT_MAIN_ANCHOR_FRESHNESS_RE.findall(joined))
    return {
        "answer_ready_seen": bool(ANSWER_READY_RE.search(joined)),
        "not_answerable_yet_seen": bool(NOT_ANSWERABLE_RE.search(joined)),
        "stale_selected_anchor_seen": stale_count > 0,
        "selected_anchor_freshness_signal_seen": selected_count > 0 or parent_main_count > 0,
        "signal_counts": {
            "answer_ready": len(ANSWER_READY_RE.findall(joined)),
            "not_answerable_yet": len(NOT_ANSWERABLE_RE.findall(joined)),
            "stale_selected_anchor": stale_count,
            "selected_anchor_freshness": selected_count,
            "parent_main_anchor_freshness": parent_main_count,
        },
        "privacy_boundary": "committed-safe counters/booleans only; raw status text is not retained",
    }


def _deep_audit_target_persistence_signals(texts: Iterable[str]) -> dict[str, Any]:
    joined = "\n".join(text for text in texts if text)
    return {
        "audit_continuation_workflow_seen": bool(AUDIT_CONTINUATION_WORKFLOW_RE.search(joined)),
        "target_persistence_signal_seen": bool(TARGET_PERSISTENCE_RE.search(joined)),
        "target_drift_signal_seen": bool(TARGET_DRIFT_RE.search(joined)),
        "target_mismatch_signal_seen": bool(TARGET_MISMATCH_RE.search(joined)),
        "signal_counts": {
            "audit_continuation_workflow": len(AUDIT_CONTINUATION_WORKFLOW_RE.findall(joined)),
            "target_persistence": len(TARGET_PERSISTENCE_RE.findall(joined)),
            "target_drift": len(TARGET_DRIFT_RE.findall(joined)),
            "target_mismatch": len(TARGET_MISMATCH_RE.findall(joined)),
        },
        "privacy_boundary": "committed-safe counters/booleans only; raw audit target text is not retained",
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
    current_loop_markers = _current_loop_markers(lane_path, metadata)
    parsed = parse_lf_delimited_json(events_path)
    final_text = final_path.read_text(encoding="utf-8", errors="replace") if final_path.exists() else ""

    event_counts: dict[str, int] = {}
    chronology: list[dict[str, Any]] = []
    completed_order: list[dict[str, Any]] = []
    completed_commands: list[str] = []
    pending_starts_by_id: dict[str, dict[str, int]] = {}
    pending_starts_by_command: dict[str, list[dict[str, int]]] = {}
    started_order = 0
    command_started = 0
    command_completed = 0
    surface_counts = {
        "jsonl_cli": 0,
        "jsonl_package_import": 0,
        "jsonl_skill_file": 0,
        "convo_skill_file": 0,
        "convo_alias": 0,
    }
    command_kind_counts: dict[str, int] = {}
    route_families: list[str] = []
    chosen_routes: list[str] = []
    wrong_reasons: list[str] = []
    read_more_mentions = 0
    semantic_debug_mentions = 0
    source_ref_signal_count = 0
    freshness_signal_texts: list[str] = [final_text]

    for event_index, event in enumerate(parsed.events):
        event_type = str(event.get("type") or "<missing>")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        raw_probe = json.dumps(event, ensure_ascii=False, sort_keys=True)
        freshness_signal_texts.append(raw_probe)
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
            start_ref = {"started_index": started_order, "started_event_index": event_index}
            started_order += 1
            item = event.get("item")
            item_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(item_id, str) and item_id:
                pending_starts_by_id[item_id] = start_ref
            pending_starts_by_command.setdefault(command, []).append(start_ref)
        else:
            start_ref = None
        if event_type == "item.completed":
            command_completed += 1
            completed_commands.append(command)
            item = event.get("item")
            item_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(item_id, str) and item_id:
                start_ref = pending_starts_by_id.pop(item_id, None)
            if start_ref is None:
                pending = pending_starts_by_command.get(command, [])
                start_ref = pending.pop(0) if pending else None
            elif start_ref in pending_starts_by_command.get(command, []):
                pending_starts_by_command[command].remove(start_ref)
        if JSONL_CLI_RE.search(command):
            surface_counts["jsonl_cli"] += 1
        if JSONL_PACKAGE_RE.search(command):
            surface_counts["jsonl_package_import"] += 1
        if JSONL_SKILL_RE.search(command):
            surface_counts["jsonl_skill_file"] += 1
        if CONVO_SKILL_RE.search(command):
            surface_counts["convo_skill_file"] += 1
        if CONVO_ALIAS_RE.search(command):
            surface_counts["convo_alias"] += 1
        if event_type == "item.completed":
            for reason in _wrong_command_reasons(command, expected_route_family=expected_route_family):
                wrong_reasons.append(reason)

        output_text = _event_command_output(event)
        partial_packet_signal = event_type == "item.completed" and bool(PARTIAL_PACKET_RE.search(output_text))
        manual_fallback_candidate = _is_broad_search(command) or command_kind == "file_read"
        evidence_classes = (
            _evidence_access_classes(
                command,
                output_text,
                command_kind,
                command_succeeded=_event_command_succeeded(event),
                current_loop_markers=current_loop_markers,
            )
            if event_type == "item.completed"
            else []
        )
        evidence_roles = _evidence_access_roles(evidence_classes)
        command_entry = {
            "event_index": event_index,
            "lifecycle_event": event_type,
            "command_kind": command_kind,
            "command_hash_12": _sha256_12_text(command),
            "jsonl_product_surface": command_kind
            in {"jsonl_read_more", "jsonl_semantic_debug", "jsonl_ask_execute", "jsonl_cli_other", "jsonl_package_import_or_module", "convo_alias"},
            "broad_search_or_listing": _is_broad_search(command),
            "partial_packet_signal": partial_packet_signal,
            "manual_fallback_candidate": manual_fallback_candidate,
            "evidence_classes": evidence_classes,
            "evidence_roles": evidence_roles,
            "started_index": start_ref["started_index"] if start_ref else None,
            "started_event_index": start_ref["started_event_index"] if start_ref else None,
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
                    "partial_packet_signal": command_entry["partial_packet_signal"],
                    "manual_fallback_candidate": command_entry["manual_fallback_candidate"],
                    "evidence_classes": command_entry["evidence_classes"],
                    "evidence_roles": command_entry["evidence_roles"],
                    "started_index": command_entry["started_index"],
                    "started_event_index": command_entry["started_event_index"],
                }
            )

        if output_text:
            freshness_signal_texts.append(output_text)
            route_families.extend(ROUTE_FAMILY_RE.findall(output_text))
            route_families.extend(JSON_ROUTE_FAMILY_RE.findall(output_text))
            chosen_routes.extend(CHOSEN_ROUTE_RE.findall(output_text))
            source_ref_signal_count += len(SOURCE_REF_RE.findall(output_text))

    product_surface_count = (
        surface_counts["jsonl_cli"] + surface_counts["jsonl_package_import"] + surface_counts["convo_alias"]
    )
    skill_file_mention_count = surface_counts["jsonl_skill_file"] + surface_counts["convo_skill_file"]
    skill_only_mentions = skill_file_mention_count > 0 and product_surface_count == 0
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
    latest_status_freshness = _latest_status_freshness_signals(freshness_signal_texts)
    deep_audit_target_persistence = _deep_audit_target_persistence_signals(freshness_signal_texts)
    contamination_chronology = _contamination_chronology(
        completed_order,
        expected_route_family=expected_route_family,
        positive_benchmark_lane=expected_activation,
        parse_errors=parsed.parse_errors,
    )
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
            "skill_file_mention_count": skill_file_mention_count,
            "surface_counts": surface_counts,
            "first_product_command": first_product,
        },
        "command_chronology": chronology,
        "command_order": completed_order,
        "contamination_chronology": contamination_chronology,
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
        "latest_status_freshness": latest_status_freshness,
        "deep_audit_target_persistence": deep_audit_target_persistence,
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


def build_receiving_agent_trace_ledger(
    lanes: Iterable[LaneSpec],
    *,
    repo_root: Path | None = None,
    trace_origin: str = "offline_replay",
) -> dict[str, Any]:
    if trace_origin not in {"offline_replay", "live_no_hint"}:
        raise ValueError(f"Unsupported trace origin: {trace_origin}")
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
        "route_cleanliness_pass_count": sum(
            1 for lane in lane_reports if lane["contamination_chronology"]["route_cleanliness"] == "pass"
        ),
        "route_cleanliness_fail_count": sum(
            1 for lane in lane_reports if lane["contamination_chronology"]["route_cleanliness"] == "fail"
        ),
        "route_cleanliness_unadjudicable_count": sum(
            1
            for lane in lane_reports
            if lane["contamination_chronology"]["route_cleanliness"] == "unadjudicable"
        ),
        "latest_status_answer_ready_seen_count": sum(
            1 for lane in lane_reports if lane["latest_status_freshness"]["answer_ready_seen"]
        ),
        "latest_status_not_answerable_yet_seen_count": sum(
            1 for lane in lane_reports if lane["latest_status_freshness"]["not_answerable_yet_seen"]
        ),
        "latest_status_stale_selected_anchor_seen_count": sum(
            1 for lane in lane_reports if lane["latest_status_freshness"]["stale_selected_anchor_seen"]
        ),
        "deep_audit_target_persistence_seen_count": sum(
            1 for lane in lane_reports if lane["deep_audit_target_persistence"]["target_persistence_signal_seen"]
        ),
        "deep_audit_target_drift_seen_count": sum(
            1 for lane in lane_reports if lane["deep_audit_target_persistence"]["target_drift_signal_seen"]
        ),
        "deep_audit_target_mismatch_seen_count": sum(
            1 for lane in lane_reports if lane["deep_audit_target_persistence"]["target_mismatch_signal_seen"]
        ),
    }
    payload = {
        "receiving_agent_trace_ledger": {
            "schema_version": "1.3",
            "privacy_boundary": (
                "committed-safe: raw prompts, final prose, stderr, command arguments, "
                "absolute paths, thread/session ids, and runtime/auth paths are omitted"
            ),
            "summary": summary,
            "lanes": lane_reports,
            "claim_boundaries": {
                "trace_origin": trace_origin,
                "offline_replay_only": trace_origin == "offline_replay",
                "live_model_send_run": trace_origin == "live_no_hint",
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
        f"- Route cleanliness pass/fail/unadjudicable: `{summary['route_cleanliness_pass_count']}` / `{summary['route_cleanliness_fail_count']}` / `{summary['route_cleanliness_unadjudicable_count']}`",
        f"- Latest-status ANSWER READY signals: `{summary['latest_status_answer_ready_seen_count']}`",
        f"- Latest-status NOT ANSWERABLE signals: `{summary['latest_status_not_answerable_yet_seen_count']}`",
        f"- Latest-status stale-anchor signals: `{summary['latest_status_stale_selected_anchor_seen_count']}`",
        f"- Deep-audit target-persistence signals: `{summary['deep_audit_target_persistence_seen_count']}`",
        f"- Deep-audit target-drift signals: `{summary['deep_audit_target_drift_seen_count']}`",
        f"- Deep-audit target-mismatch signals: `{summary['deep_audit_target_mismatch_seen_count']}`",
        "",
    ]
    for lane in ledger["lanes"]:
        counts = lane["command_counts"]
        route = lane["route_selection"]
        final = lane["final_answer"]
        freshness = lane["latest_status_freshness"]
        audit_target = lane["deep_audit_target_persistence"]
        contamination = lane["contamination_chronology"]
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
                f"- Latest-status freshness: answer-ready `{freshness['answer_ready_seen']}`, not-answerable `{freshness['not_answerable_yet_seen']}`, stale-anchor `{freshness['stale_selected_anchor_seen']}`, freshness-signal `{freshness['selected_anchor_freshness_signal_seen']}`",
                f"- Deep-audit target persistence: workflow `{audit_target['audit_continuation_workflow_seen']}`, target-persistence `{audit_target['target_persistence_signal_seen']}`, target-drift `{audit_target['target_drift_signal_seen']}`, target-mismatch `{audit_target['target_mismatch_signal_seen']}`",
                f"- Source follow-through: `{lane['source_follow_through']['status']}`; primary-ref signal `{lane['source_follow_through']['primary_ref_use']}`",
                f"- Route cleanliness: `{contamination['route_cleanliness']}` (`{contamination['classification']}`)",
                f"- First primary/current-loop started index: `{contamination['first_primary_raw_evidence_started_index']}` / `{contamination['first_current_loop_material_started_index']}`",
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
                f"  - `{entry['completed_index']}` `{entry['command_kind']}` hash `{entry['command_hash_12']}` classes `{', '.join(entry['evidence_classes']) or 'none'}`"
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
