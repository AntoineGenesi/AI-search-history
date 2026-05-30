from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INLINE_CANDIDATE_LIMIT = 12
INLINE_CONTEXT_BYTE_LIMIT = 24_000


@dataclass(frozen=True)
class CandidateRow:
    rank: int
    candidate_id: str
    source_ref: dict
    path: str
    line: int
    timestamp: str | None
    timestamp_utc: str | None
    role: str | None
    session: str | None
    cwd: str | None
    snippet: str
    score: float
    confidence: str
    matched_terms: list[str]
    stages: list[str]
    primary_stage: str
    reasons: list[str]
    reason_codes: list[str]
    artifacts: list[str]
    tasks: list[str]

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "candidate_id": self.candidate_id,
            "source_ref": self.source_ref,
            "path": self.path,
            "line": self.line,
            "timestamp": self.timestamp,
            "timestamp_utc": self.timestamp_utc,
            "role": self.role,
            "session": self.session,
            "cwd": self.cwd,
            "snippet": self.snippet,
            "score": self.score,
            "confidence": self.confidence,
            "matched_terms": self.matched_terms,
            "stages": self.stages,
            "stage": self.primary_stage,
            "reasons": self.reasons,
            "reason_codes": self.reason_codes,
            "artifacts": self.artifacts,
            "tasks": self.tasks,
        }


def build_agent_find_payload(
    *,
    source_plan: dict,
    search_result: dict,
    manifest_report: dict | None = None,
    output_format: str = "json",
    report_output: str | None = None,
) -> dict[str, Any]:
    candidates = build_candidate_rows(search_result)
    timeline = build_timeline(candidates)
    candidate_dicts = [candidate.to_dict() for candidate in candidates]
    context_bytes = len(str(candidate_dicts).encode("utf-8"))
    raw_baseline_bytes = search_result.get("searched_bytes", 0)
    estimated_reduction_ratio = reduction_ratio(context_bytes, raw_baseline_bytes)
    status = "found" if candidates else "no_evidence"
    agent_find = {
        "schema_version": 1,
        "status": status,
        "query": source_plan["query"],
        "command_surface": command_surface(),
        "plan_sources": build_plan_sources(source_plan, search_result, manifest_report),
        "broaden_query": build_broaden_query(source_plan["query"]),
        "find_candidates": candidate_dicts[:INLINE_CANDIDATE_LIMIT],
        "timeline": [candidate.to_dict() for candidate in timeline[:INLINE_CANDIDATE_LIMIT]],
        "cluster_candidates": build_cluster_candidates(search_result),
        "evidence_bundles": build_evidence_bundles(search_result),
        "no_evidence_report": build_no_evidence_report(
            status=status,
            source_plan=source_plan,
            search_result=search_result,
            manifest_report=manifest_report,
        ),
        "next_search_suggestions": next_search_suggestions(status=status, source_plan=source_plan, candidates=candidates),
        "report_policy": report_policy(
            candidate_count=len(candidates),
            context_bytes=context_bytes,
            raw_baseline_bytes=raw_baseline_bytes,
            estimated_reduction_ratio=estimated_reduction_ratio,
            output_format=output_format,
            report_output=report_output,
        ),
        "context_budget": {
            "payload_bytes": context_bytes,
            "estimated_tokens": estimate_tokens(context_bytes),
            "raw_baseline_bytes": raw_baseline_bytes,
            "estimated_reduction_ratio": estimated_reduction_ratio,
            "report_recommended": context_bytes > INLINE_CONTEXT_BYTE_LIMIT or len(candidates) > INLINE_CANDIDATE_LIMIT,
            "report_written": report_output,
        },
        "token_efficiency": {
            "inline_candidate_limit": INLINE_CANDIDATE_LIMIT,
            "candidate_count": len(candidates),
            "inline_context_bytes": context_bytes,
            "searched_files": search_result.get("searched_files", 0),
            "searched_bytes": search_result.get("searched_bytes", 0),
        },
    }
    return {"agent_find": agent_find, "source_plan": source_plan}


def command_surface() -> dict[str, str]:
    return {
        "plan-sources": "Explains selected/rejected roots, caps, ordering, wrong-root warnings, and optional manifest shortlist metadata.",
        "find-candidates": "Returns compact candidate rows with source refs, timestamps, roles, snippets, stages, reasons, and confidence.",
        "broaden-query": "Shows exact, normalized, synonym/paraphrase, artifact/path, cwd/project, date-window, role, and future fuzzy/semantic families.",
        "timeline": "Sorts candidate rows by parsed message timestamp with deterministic path/line tie-breaking.",
        "cluster-candidates": "Groups candidates by task, artifact, preference, session, or path for compact AI handoff.",
        "no-evidence-report": "Reports searched scope, excluded scope, weak candidates, warnings, and next safe searches instead of hallucinating.",
    }


def build_candidate_rows(search_result: dict) -> list[CandidateRow]:
    rows: list[CandidateRow] = []
    for group in search_result.get("groups", []):
        for match in group.get("matches", []):
            reasons = list(match.get("reasons") or [])
            stages = stages_from_reasons(reasons)
            matched_terms = matched_terms_from_reasons(reasons)
            stages = stages or ["unsupported_or_score_only"]
            rows.append(
                CandidateRow(
                    rank=0,
                    candidate_id="",
                    source_ref={
                        "path": match.get("path") or "",
                        "line": int(match.get("line") or 0),
                        "session": match.get("session") or group.get("session"),
                    },
                    path=match.get("path") or "",
                    line=int(match.get("line") or 0),
                    timestamp=match.get("timestamp"),
                    timestamp_utc=timestamp_utc(match.get("timestamp")),
                    role=match.get("role"),
                    session=match.get("session") or group.get("session"),
                    cwd=match.get("cwd"),
                    snippet=match.get("snippet") or "",
                    score=float(match.get("score") or 0),
                    confidence=confidence_for_score(float(match.get("score") or 0), stages),
                    matched_terms=matched_terms,
                    stages=stages,
                    primary_stage=primary_stage(stages),
                    reasons=reasons,
                    reason_codes=reason_codes_from_reasons(reasons),
                    artifacts=list(match.get("artifacts") or [])[:20],
                    tasks=list(match.get("tasks") or [])[:20],
                )
            )
    rows.sort(key=lambda item: (item.score, item.timestamp or "", item.path, -item.line), reverse=True)
    return [replace(row, rank=index + 1, candidate_id=f"c{index + 1:03d}") for index, row in enumerate(rows)]


def build_timeline(candidates: list[CandidateRow]) -> list[CandidateRow]:
    return sorted(
        candidates,
        key=lambda item: (timestamp_sort_key(item.timestamp), item.path, item.line),
    )


def timestamp_sort_key(value: str | None) -> str:
    if not value:
        return "9999-12-31T23:59:59+00:00"
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        return value


def timestamp_utc(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def stages_from_reasons(reasons: list[str]) -> list[str]:
    stages: set[str] = set()
    for reason in reasons:
        if reason.startswith("text:"):
            stages.add("exact_or_expanded_text")
        elif reason.startswith("role:"):
            stages.add("role")
        elif reason == "date-window":
            stages.add("date_window")
        elif reason == "cwd":
            stages.add("cwd_project")
        elif reason == "artifact":
            stages.add("artifact_path")
        elif reason == "bounded-no-term":
            stages.add("bounded_no_term")
    return sorted(stages)


def primary_stage(stages: list[str]) -> str:
    for preferred in ("artifact_path", "exact_or_expanded_text", "date_window", "cwd_project", "role"):
        if preferred in stages:
            return preferred
    return stages[0] if stages else "unsupported"


def reason_codes_from_reasons(reasons: list[str]) -> list[str]:
    codes = []
    for reason in reasons:
        if reason.startswith("text:"):
            codes.append("normalized_term")
        elif reason.startswith("role:"):
            codes.append("role_match")
        elif reason == "date-window":
            codes.append("date_window")
        elif reason == "cwd":
            codes.append("project_hint")
        elif reason == "artifact":
            codes.append("artifact_path")
        elif reason == "bounded-no-term":
            codes.append("bounded_no_term")
        else:
            codes.append(reason)
    return sorted(set(codes))


def matched_terms_from_reasons(reasons: list[str]) -> list[str]:
    terms = []
    for reason in reasons:
        if reason.startswith("text:"):
            terms.append(reason.split(":", 1)[1])
    return sorted(set(terms))


def confidence_for_score(score: float, stages: list[str]) -> str:
    if score >= 22 and ("exact_or_expanded_text" in stages or "artifact_path" in stages):
        return "high"
    if score >= 12:
        return "medium"
    if score > 0:
        return "low"
    return "weak"


def build_plan_sources(source_plan: dict, search_result: dict, manifest_report: dict | None) -> dict:
    sources = source_plan["sources"]
    wrong_root_warnings = [
        item["reason"]
        for item in sources.get("rejected_roots", [])
        if "root" in item.get("reason", "") or "dangerous" in item.get("reason", "")
    ]
    payload = {
        "roots": sources.get("roots", []),
        "rejected_roots": sources.get("rejected_roots", []),
        "limits": sources.get("limits", {}),
        "warnings": search_result.get("warnings", []),
        "blind_spots": search_result.get("blind_spots", []),
        "wrong_root_warnings": sorted(set(wrong_root_warnings)),
        "searched_files": search_result.get("searched_files", 0),
        "searched_bytes": search_result.get("searched_bytes", 0),
        "ordering": "scanner score desc, timestamp desc; timeline section is timestamp asc",
    }
    if manifest_report:
        report = manifest_report.get("manifest_report", {})
        payload["manifest_shortlist"] = {
            "entry_count": report.get("manifest", {}).get("entry_count", 0),
            "metrics": report.get("shortlist", {}).get("metrics", {}),
            "selected_entries": report.get("shortlist", {}).get("selected_entries", [])[:INLINE_CANDIDATE_LIMIT],
            "excluded_entries": report.get("shortlist", {}).get("excluded_entries", [])[:INLINE_CANDIDATE_LIMIT],
        }
    return payload


def build_broaden_query(query: dict) -> dict:
    expanded_terms = []
    for term in query.get("terms", []):
        expanded_terms.append(
            {
                "term": term,
                "families": query.get("expansion_reasons", {}).get(term) or ["exact"],
            }
        )
    return {
        "original": query.get("original", ""),
        "normalized": query.get("normalized", ""),
        "base_terms": query.get("base_terms", []),
        "expanded_terms": expanded_terms,
        "artifact_hints": query.get("artifact_hints", []),
        "role": query.get("role"),
        "date_window": query.get("date_window", {}),
        "defined_families": [
            "exact",
            "normalized",
            "synonym_paraphrase",
            "artifact_path",
            "cwd_project",
            "date_window",
            "role",
            "typo_fuzzy_future",
            "semantic_local_future",
        ],
        "unsupported_stages": {
            "typo_fuzzy": {"status": "not_run", "reason": "planned for jsh-012"},
            "semantic_local": {"status": "not_run", "reason": "no embedding backend in jsh-007"},
        },
    }


def build_cluster_candidates(search_result: dict) -> list[dict]:
    clusters = []
    for index, group in enumerate(search_result.get("groups", []), 1):
        clusters.append(
            {
                "rank": index,
                "cluster_key": group.get("cluster_key"),
                "cluster_type": group.get("cluster_type"),
                "cluster_value": group.get("cluster_value"),
                "top_score": group.get("top_score"),
                "paths": group.get("paths", []),
                "session": group.get("session"),
                "tasks": list(group.get("tasks", []))[:20],
                "artifacts": list(group.get("artifacts", []))[:20],
                "match_count": len(group.get("matches", [])),
            }
        )
    return clusters[:INLINE_CANDIDATE_LIMIT]


def build_evidence_bundles(search_result: dict) -> list[dict]:
    bundles = []
    for index, group in enumerate(search_result.get("groups", []), 1):
        matches = group.get("matches", [])
        top_refs = [
            {
                "path": match.get("path"),
                "line": match.get("line"),
                "timestamp": match.get("timestamp"),
                "role": match.get("role"),
            }
            for match in matches[:3]
        ]
        bundles.append(
            {
                "bundle_id": f"b{index:03d}",
                "cluster_key": group.get("cluster_key"),
                "cluster_type": group.get("cluster_type"),
                "cluster_value": group.get("cluster_value"),
                "top_score": group.get("top_score"),
                "summary": {
                    "match_count": len(matches),
                    "paths": group.get("paths", []),
                    "tasks": list(group.get("tasks", []))[:20],
                    "artifacts": list(group.get("artifacts", []))[:20],
                },
                "top_source_refs": top_refs,
            }
        )
    return bundles[:INLINE_CANDIDATE_LIMIT]


def build_no_evidence_report(
    *,
    status: str,
    source_plan: dict,
    search_result: dict,
    manifest_report: dict | None,
) -> dict:
    if status != "no_evidence":
        return {
            "needed": False,
            "reason": "Candidates were found; inspect find_candidates, timeline, and cluster_candidates.",
        }
    weak_candidates = []
    if manifest_report:
        selected = manifest_report.get("manifest_report", {}).get("shortlist", {}).get("selected_entries", [])
        for item in selected[:INLINE_CANDIDATE_LIMIT]:
            weak_candidates.append(
                {
                    "path": item.get("entry", {}).get("path"),
                    "score": item.get("score"),
                    "reasons": item.get("reasons", []),
                }
            )
    return {
        "needed": True,
        "answer": "not_found_yet",
        "searched_scope": {
            "roots": source_plan["sources"].get("roots", []),
            "searched_files": search_result.get("searched_files", 0),
            "searched_bytes": search_result.get("searched_bytes", 0),
        },
        "excluded_scope": source_plan["sources"].get("rejected_roots", []),
        "weak_candidates": weak_candidates,
        "warnings": search_result.get("warnings", []),
        "blind_spots": search_result.get("blind_spots", []),
        "why_not_confident": "No matching candidate rows survived the current bounded source plan and query stages.",
    }


def next_search_suggestions(*, status: str, source_plan: dict, candidates: list[CandidateRow]) -> list[str]:
    suggestions: list[str] = []
    query = source_plan.get("query", {})
    if not query.get("date_window", {}).get("start"):
        suggestions.append("Add an approximate date window if the user remembers when this happened.")
    if not query.get("role"):
        suggestions.append("Add --role user or --role assistant when the remembered speaker matters.")
    if not query.get("artifact_hints"):
        suggestions.append("Add a file, task slug, report name, or project folder hint if available.")
    if status == "no_evidence":
        suggestions.append("Run a broader fuzzy/semantic pass after jsh-012 before declaring permanent no evidence.")
    elif len(candidates) > INLINE_CANDIDATE_LIMIT:
        suggestions.append("Use the top clusters or write a report instead of opening every candidate.")
    return suggestions


def report_policy(
    *,
    candidate_count: int,
    context_bytes: int,
    raw_baseline_bytes: int,
    estimated_reduction_ratio: float,
    output_format: str,
    report_output: str | None,
) -> dict:
    should_write = bool(report_output) or candidate_count > INLINE_CANDIDATE_LIMIT or context_bytes > INLINE_CONTEXT_BYTE_LIMIT
    return {
        "inline_candidate_limit": INLINE_CANDIDATE_LIMIT,
        "inline_context_byte_limit": INLINE_CONTEXT_BYTE_LIMIT,
        "candidate_count": candidate_count,
        "estimated_context_bytes": context_bytes,
        "estimated_tokens": estimate_tokens(context_bytes),
        "raw_baseline_bytes": raw_baseline_bytes,
        "estimated_reduction_ratio": estimated_reduction_ratio,
        "should_write_report": should_write,
        "report_output": report_output,
        "format": output_format,
        "rule": "write a report when candidates exceed inline limits, context bytes exceed the budget, or --report-output is provided",
    }


def estimate_tokens(byte_count: int) -> int:
    return max(1, round(byte_count / 4))


def reduction_ratio(value: int, baseline: int) -> float:
    if baseline <= 0:
        return 0.0
    return round(max(0.0, 1 - (value / baseline)), 3)
