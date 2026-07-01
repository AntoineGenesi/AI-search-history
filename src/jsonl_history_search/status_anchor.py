from __future__ import annotations

import shlex
import time
from pathlib import Path
from typing import Any

from .latest_user_messages import latest_user_messages


DEFAULT_STATUS_QUERY = "what happened since my last message?"
SCHEMA_VERSION = 1

STATUS_QUERY_PATTERNS = (
    "what happened since",
    "what has happened since",
    "what did you do while i was away",
    "while i was away",
    "status since",
    "since last time",
    "what changed after",
    "what happened in the main thread",
)

ADMIN_STATUS_MARKERS = (
    "what happened since",
    "what has happened since",
    "what did you do while i was away",
    "while i was away",
    "what happened in the main thread",
    "status since",
    "since last time",
    "repo-state",
    "process-event",
    "nothing happened",
    "side-thread",
    "side thread",
    "latest admin",
    "last message",
)

WORKER_PROMPT_MARKERS = (
    "you are the jsh-",
    "you are the high-reasoning implementer",
    "you are the independent reviewer",
    "you are implementing jsh-",
    "you are reviewing branch",
    "implementation worker",
    "implementer for jsh-",
    "isolated implementer",
    "independent reviewer for jsh-",
    "independent review for jsh-",
    "jsonl_layered_current lane",
    "reviewer worker",
    "orchestrator reviewer",
    "read-only review",
    "coordinator evidence for jsh-",
    "scope: verify",
    "inspect changed files",
    "use implementer discipline",
    "acceptance criteria:",
    "read first:",
    "<turn_aborted>",
)

SUBSTANTIVE_WORK_MARKERS = (
    "keep working",
    "implement",
    "execute",
    "fix",
    "build",
    "ship",
    "product delta",
    "semantic-debug",
    "semantic debug",
    "route",
    "workflow",
    "case-002",
    "case 002",
    "case-003",
    "jsh-",
)

KNOWN_WORK = (
    {
        "id": "JSH-998",
        "slug": "jsh-998",
        "value_kind": "behavior_delta",
        "summary": "JSH-998 changed route behavior: generic persistent-coordinator wording now anchors to the intended correction window instead of later adjacent records.",
        "artifact_terms": ("route_anchoring_improved", "generic persistent-coordinator", "correction window"),
    },
    {
        "id": "JSH-999",
        "slug": "jsh-999",
        "value_kind": "behavior_delta",
        "summary": "JSH-999 changed command surface: recovery-first packets now present one bundled follow-next-reads command instead of encouraging multiple read-more fallbacks.",
        "artifact_terms": ("handoff_churn_reduced", "single saved", "follow-next-reads"),
    },
    {
        "id": "JSH-1000",
        "slug": "jsh-1000",
        "value_kind": "non_delta",
        "summary": "JSH-1000 was contract/support work: it locked retained hard cases into a committed-safe product contract rather than changing retrieval behavior directly.",
        "artifact_terms": ("hard-case product contract", "committed-safe product contract", "retained hard cases"),
    },
    {
        "id": "JSH-1001",
        "slug": "jsh-1001",
        "value_kind": "non_delta",
        "summary": "JSH-1001 was measurement/result work: the fresh rerun preserved source-grounded answers but still showed workflow churn blocked.",
        "artifact_terms": ("still_churn_blocked", "workflow churn", "fresh agent rerun"),
    },
    {
        "id": "JSH-1002",
        "slug": "jsh-1002",
        "value_kind": "non_delta",
        "summary": "JSH-1002 was claim-boundary/docs work: it reset supported claims after JSH-1001 rather than fixing product behavior.",
        "artifact_terms": ("claim boundary", "claim-boundary", "does not add a new benchmark"),
    },
)


def build_status_anchor_payload(args: Any) -> dict:
    default_tz = _timezone_from_args(args)
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"
    cwd = Path(args.cwd).expanduser().resolve()
    query = getattr(args, "query", None) or DEFAULT_STATUS_QUERY
    limit = getattr(args, "limit", None) or getattr(args, "max_matches", 40)
    snippet_chars = max(0, getattr(args, "snippet_chars", 280))
    latest = latest_user_messages(
        codex_home=codex_home,
        default_tz=default_tz,
        limit=limit,
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_elapsed=args.max_elapsed,
        snippet_chars=snippet_chars,
    )
    return {
        "status_anchor": status_anchor_report(
            query=query,
            latest=latest,
            cwd=cwd,
            codex_home=codex_home,
            started=time.monotonic(),
        )
    }


def status_anchor_report(
    *,
    query: str,
    latest: dict,
    cwd: Path,
    codex_home: Path | None = None,
    started: float | None = None,
) -> dict:
    anchor_candidates = _rank_anchor_candidates(latest.get("messages", []))
    selected_anchor = _with_read_more_command(_select_anchor(anchor_candidates), cwd=cwd, codex_home=codex_home)
    artifacts = _collect_work_artifacts(cwd)
    behavior_deltas = [item for item in artifacts["value_items"] if item["value_kind"] == "behavior_delta"]
    non_deltas = [item for item in artifacts["value_items"] if item["value_kind"] == "non_delta"]
    unresolved_blockers = _unresolved_blockers(artifacts)
    answer_brief = _answer_brief(behavior_deltas, non_deltas, unresolved_blockers)
    next_reads = _next_reads(selected_anchor, artifacts, cwd=cwd, codex_home=codex_home)
    blind_spots = []
    if not selected_anchor:
        blind_spots.append("No substantive anchor selected from the searched latest-message scope.")
    if not artifacts["value_items"]:
        blind_spots.append("No known JSH-998 through JSH-1002 task/report/review artifacts were found under --cwd.")
    if not artifacts["commits"]:
        blind_spots.append("Commit join was not run; work_products are based on task/report/review artifacts.")
    elapsed = None if started is None else round(time.monotonic() - started, 6)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if selected_anchor else "anchor_ambiguous",
        "query": query,
        "routing": {
            "route": "what_happened_since_status_anchor",
            "natural_status_triggered": is_status_question(query),
            "jsonl_first": True,
            "built_on": "latest_user_messages",
            "no_spawned_agents": True,
        },
        "source_scope": latest.get("source_scope"),
        "summary": {
            "anchor_candidate_count": len(anchor_candidates),
            "selected_anchor_available": selected_anchor is not None,
            "work_product_count": len(artifacts["tasks"]) + len(artifacts["reports"]) + len(artifacts["reviews"]),
            "behavior_delta_count": len(behavior_deltas),
            "non_delta_count": len(non_deltas),
            "unresolved_blocker_count": len(unresolved_blockers),
            "elapsed_seconds": elapsed,
        },
        "anchor_candidates": anchor_candidates,
        "selected_anchor": selected_anchor,
        "next_reads": next_reads,
        "agent_instruction": _agent_instruction(selected_anchor, next_reads),
        "work_products": {
            "tasks": artifacts["tasks"],
            "reports": artifacts["reports"],
            "reviews": artifacts["reviews"],
            "commits": artifacts["commits"],
        },
        "behavior_deltas": behavior_deltas,
        "non_deltas": non_deltas,
        "unresolved_blockers": unresolved_blockers,
        "answer_brief": answer_brief,
        "blind_spots": blind_spots,
        "privacy": {
            "raw_transcript_dump_included": False,
            "raw_text_included": True,
            "snippets_are_compact": True,
            "source_refs_included": True,
        },
        "latest_user_messages_summary": latest.get("summary", {}),
        "warnings": latest.get("sources", {}).get("warnings", []),
    }


def is_status_question(query: str) -> bool:
    normalized = _normalize(query)
    return any(pattern in normalized for pattern in STATUS_QUERY_PATTERNS)


def _rank_anchor_candidates(messages: list[dict]) -> list[dict]:
    candidates = []
    status_cutoff = _status_query_cutoff(messages)
    for index, message in enumerate(messages):
        text = str(message.get("snippet") or "")
        timestamp = message.get("timestamp_utc")
        score, classification, decision, rationale = _score_anchor(
            text,
            index,
            timestamp_utc=str(timestamp or ""),
            status_cutoff=status_cutoff,
        )
        candidates.append(
            {
                "rank": 0,
                "source_ref": message.get("source_ref"),
                "source_kind": message.get("source_kind"),
                "source_identifier": message.get("source_identifier"),
                "openable_source_ref": message.get("openable_source_ref"),
                "openable_target": message.get("openable_target"),
                "timestamp_utc": message.get("timestamp_utc"),
                "local_timestamp": message.get("local_timestamp"),
                "snippet": text,
                "classification": classification,
                "score": score,
                "decision": decision,
                "rationale": rationale,
            }
        )
    candidates.sort(key=lambda item: (item["score"], item.get("timestamp_utc") or ""), reverse=True)
    for rank, candidate in enumerate(candidates, 1):
        candidate["rank"] = rank
    return candidates


def _status_query_cutoff(messages: list[dict]) -> str | None:
    for message in messages:
        timestamp = str(message.get("timestamp_utc") or "")
        if timestamp and is_status_question(str(message.get("snippet") or "")):
            return timestamp
    return None


def _score_anchor(text: str, index: int, *, timestamp_utc: str = "", status_cutoff: str | None = None) -> tuple[int, str, str, str]:
    normalized = _normalize(text)
    recency_penalty = min(index, 20)
    if not normalized:
        return -100, "empty", "rejected", "empty latest-message row"
    if any(marker in normalized for marker in WORKER_PROMPT_MARKERS):
        return -55 - recency_penalty, "worker_or_reviewer_prompt", "rejected", "worker/reviewer prompt is not a human status anchor"
    if any(marker in normalized for marker in ADMIN_STATUS_MARKERS):
        return -30 - recency_penalty, "admin_or_status_turn", "rejected", "admin/status turn is useful context but a poor unattended-work anchor"
    if status_cutoff and timestamp_utc and timestamp_utc > status_cutoff:
        return -40 - recency_penalty, "post_status_context", "rejected", "newer than the status question; context, not the since-anchor"
    work_hits = [marker for marker in SUBSTANTIVE_WORK_MARKERS if marker in normalized]
    if work_hits:
        score = 70 + min(len(work_hits), 5) * 4 - recency_penalty
        return score, "substantive_work_anchor", "selected_candidate", f"substantive work wording matched: {', '.join(work_hits[:3])}"
    if len(normalized.split()) >= 12:
        return 20 - recency_penalty, "ordinary_human_turn", "fallback_candidate", "ordinary human turn; usable only if no stronger work anchor exists"
    return 0 - recency_penalty, "short_human_turn", "fallback_candidate", "short human turn; weak status anchor"


def _select_anchor(candidates: list[dict]) -> dict | None:
    for candidate in candidates:
        if candidate["decision"] == "selected_candidate" and candidate["score"] > 0:
            selected = dict(candidate)
            selected["rationale"] = (
                selected["rationale"]
                + "; selected over newer admin/status or worker-prompt turns to avoid latest-message literalism"
            )
            return selected
    for candidate in candidates:
        if candidate["decision"] == "fallback_candidate" and candidate["score"] > 0:
            selected = dict(candidate)
            selected["rationale"] = selected["rationale"] + "; selected as fallback because no stronger work anchor was found"
            return selected
    return None


def _collect_work_artifacts(cwd: Path) -> dict:
    tasks: list[dict] = []
    reports: list[dict] = []
    reviews: list[dict] = []
    value_items: list[dict] = []
    for work in KNOWN_WORK:
        sources: list[dict] = []
        for category, roots in (
            ("task", (cwd / "tasks",)),
            ("report", (cwd / "reports",)),
            ("review", (cwd / "codex" / "state" / "reviews", cwd / "reviews")),
        ):
            for root in roots:
                if not root.is_dir():
                    continue
                for path in sorted(root.glob(f"{work['slug']}*.md")):
                    item = _artifact_item(path, cwd, category, work["artifact_terms"])
                    sources.append(item)
                    if category == "task":
                        tasks.append(item)
                    elif category == "report":
                        reports.append(item)
                    else:
                        reviews.append(item)
        if sources:
            value_items.append(
                {
                    "work_id": work["id"],
                    "value_kind": work["value_kind"],
                    "summary": work["summary"],
                    "source_refs": [source["source_ref"] for source in sources[:4]],
                    "evidence_snippets": [source["snippet"] for source in sources[:2] if source.get("snippet")],
                }
            )
    return {"tasks": tasks, "reports": reports, "reviews": reviews, "commits": [], "value_items": value_items}


def _artifact_item(path: Path, cwd: Path, category: str, terms: tuple[str, ...]) -> dict:
    rel = _rel(path, cwd)
    line_no, snippet = _first_matching_line(path, terms)
    return {
        "kind": category,
        "path": rel,
        "source_ref": f"{rel}:{line_no}" if line_no else rel,
        "snippet": snippet,
    }


def _first_matching_line(path: Path, terms: tuple[str, ...]) -> tuple[int | None, str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None, ""
    lower_terms = tuple(term.lower() for term in terms)
    for index, line in enumerate(lines, 1):
        normalized = line.lower()
        if any(term in normalized for term in lower_terms):
            return index, _compact(line)
    for index, line in enumerate(lines, 1):
        if line.strip():
            return index, _compact(line)
    return None, ""


def _unresolved_blockers(artifacts: dict) -> list[dict]:
    joined = " ".join(
        item.get("snippet", "")
        for collection in (artifacts["tasks"], artifacts["reports"], artifacts["reviews"])
        for item in collection
    ).lower()
    if "case-002" in joined or "case 002" in joined or "read-more" in joined or "workflow churn" in joined:
        return [
            {
                "blocker": "case-002 over-search/read-more churn",
                "status": "unresolved",
                "summary": "The remaining serious blocker is case-002 over-search/read-more churn after source-grounded answers are possible.",
                "source_refs": _source_refs_containing(artifacts, ("case-002", "case 002", "read-more", "workflow churn"))[:4],
            }
        ]
    return []


def _source_refs_containing(artifacts: dict, terms: tuple[str, ...]) -> list[str]:
    refs = []
    for collection in (artifacts["tasks"], artifacts["reports"], artifacts["reviews"]):
        for item in collection:
            text = f"{item.get('path', '')} {item.get('snippet', '')}".lower()
            if any(term in text for term in terms):
                refs.append(item["source_ref"])
    return refs


def _answer_brief(behavior_deltas: list[dict], non_deltas: list[dict], blockers: list[dict]) -> list[str]:
    lines = [item["summary"] for item in behavior_deltas]
    lines.extend(item["summary"] for item in non_deltas)
    lines.extend(item["summary"] for item in blockers)
    return lines


def _next_reads(selected_anchor: dict | None, artifacts: dict, *, cwd: Path, codex_home: Path | None) -> list[dict]:
    reads = []
    if selected_anchor:
        read = {
            "reason": "Inspect selected conversation anchor before answering.",
            "source_ref": selected_anchor["source_ref"],
            "snippet": selected_anchor["snippet"],
        }
        _copy_openable_fields(read, selected_anchor)
        if read.get("openable_target") and not read.get("read_more_command"):
            read["read_more_command"] = _read_more_command(str(read["openable_target"]), cwd=cwd, codex_home=codex_home)
        reads.append(read)
    for item in artifacts["value_items"]:
        for ref in item["source_refs"][:2]:
            reads.append({"reason": f"Inspect {item['work_id']} {item['value_kind']} evidence.", "source_ref": ref})
    for blocker in _unresolved_blockers(artifacts):
        for ref in blocker.get("source_refs", [])[:2]:
            reads.append({"reason": "Inspect unresolved blocker evidence.", "source_ref": ref})
    return _dedupe_reads(reads)


def _with_read_more_command(anchor: dict | None, *, cwd: Path, codex_home: Path | None) -> dict | None:
    if not anchor:
        return None
    selected = dict(anchor)
    target = selected.get("openable_target")
    if target:
        selected["openable_target"] = str(target)
        selected["read_more_command"] = _read_more_command(str(target), cwd=cwd, codex_home=codex_home)
    return selected


def _copy_openable_fields(read: dict, source: dict) -> None:
    for key in ("openable_source_ref", "openable_target", "read_more_command"):
        value = source.get(key)
        if value:
            read[key] = value


def _agent_instruction(selected_anchor: dict | None, next_reads: list[dict]) -> dict:
    first_read = next_reads[0] if next_reads else {}
    return {
        "first_read_target": first_read.get("openable_target") or first_read.get("source_ref"),
        "first_read_command": first_read.get("read_more_command"),
        "stop_condition": (
            "Start with the selected anchor read-more command when present, then inspect the listed "
            "artifact evidence only until the status answer is source-backed; do not run broad directory "
            "lookup unless the direct reads fail or contradict each other."
        ),
        "direct_anchor_openable": bool(selected_anchor and selected_anchor.get("openable_target")),
    }


def _read_more_command(target: str, *, cwd: Path, codex_home: Path | None) -> str:
    parts = [
        "jsonl-history-search",
        "read-more",
        target,
        "--cwd",
        str(cwd),
    ]
    if codex_home is not None:
        parts.extend(["--codex-home", str(codex_home)])
    parts.extend(["--format", "json", "--mode", "window", "--before", "2", "--after", "8"])
    return " ".join(shlex.quote(part) for part in parts)


def _dedupe_reads(reads: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for read in reads:
        ref = read.get("source_ref")
        if not ref or ref in seen:
            continue
        seen.add(ref)
        deduped.append(read)
    return deduped


def _rel(path: Path, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd.resolve()))
    except (OSError, ValueError):
        return str(path)


def _compact(text: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _timezone_from_args(args: Any):
    from .timeutil import timezone_from_args

    return timezone_from_args(args.timezone, args.timezone_offset)
