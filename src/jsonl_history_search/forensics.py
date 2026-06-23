from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

from .artifacts import ArtifactStatus, inspect_artifacts, resolve_artifact_path
from .claims import (
    BLOCKED,
    CHANGED,
    CLAIMED,
    FAILED,
    MERGED,
    PARTIALLY_VERIFIED,
    RUNNING,
    UNSUPPORTED,
    VERIFIED,
    classify_claim_action,
    parse_review_verdict,
)
from .gittools import (
    branch_metadata,
    commit_lifecycle,
    commit_metadata,
    current_branch,
    git_root_for,
    last_change_for_path,
    worktree_metadata,
)
from .masking import MaskContext
from .query import QueryIntent, infer_query
from .redaction import RedactionContext
from .scanner import search
from .sources import Limits, build_source_plan
from .timeutil import parse_datetime, parse_now, timezone_from_args


AROUND_WINDOW_MINUTES = 30
MAX_ARTIFACT_BYTES = 200_000
MAX_LIFECYCLE_COMMITS = 5


@dataclass(frozen=True)
class ForensicsHints:
    commit: str | None
    path: str | None
    task: str | None
    branch: str | None
    around: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def build_forensics_payload(args) -> dict:
    tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, tz)
    cwd = Path(args.cwd).expanduser().resolve()
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"
    claude_home = Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude"
    limits = Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=args.max_matches,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files,
        hard_cap_bytes=args.hard_cap_bytes,
    )
    hints = ForensicsHints(
        commit=args.commit,
        path=args.path,
        task=args.task,
        branch=args.branch,
        around=args.around,
    )
    warnings: list[str] = []
    repo = git_root_for(cwd)
    if repo is None:
        warnings.append(f"No git repository found for cwd: {cwd}")

    git_payload, git_warnings, inferred_around = _git_payload(cwd=cwd, repo=repo, hints=hints)
    warnings.extend(git_warnings)
    around_value = hints.around or inferred_around
    around_dt = parse_datetime(around_value, tz) if around_value else None
    if around_value and around_dt is None:
        warnings.append(f"Could not parse --around timestamp: {around_value}")

    query_text = _deterministic_query(hints, git_payload)
    intent = infer_query(query_text, now=now, role=None)
    if around_dt is not None:
        _apply_around_window(intent, around_dt.astimezone(tz))

    plan = build_source_plan(
        cwd=cwd,
        codex_home=codex_home,
        claude_home=claude_home,
        roots=[Path(item).expanduser() for item in args.root] if args.root else None,
        allow_arbitrary_root=args.allow_arbitrary_root,
        limits=limits,
    )
    source_plan = {
        "query": intent.to_dict(),
        "sources": plan.to_dict(),
        "cwd": str(cwd),
        "codex_home": str(codex_home),
        "claude_home": str(claude_home),
        "now": now.isoformat(),
    }
    mask_context = _mask_context_from_args(args)
    jsonl_result = search(
        plan,
        intent,
        default_tz=tz,
        cwd=str(cwd),
        mask_context=mask_context,
    )
    warnings.extend(jsonl_result.get("warnings", []))
    warnings.extend(intent.warnings)
    jsonl_evidence = _classify_jsonl_evidence(jsonl_result, cwd=cwd, repo=repo)

    artifact_checks = _artifact_checks(
        hints=hints,
        git_payload=git_payload,
        jsonl_evidence=jsonl_evidence,
        cwd=cwd,
        repo=repo,
    )
    review_evidence = _discover_review_evidence(hints.task, cwd=cwd, repo=repo)
    warnings.extend(_artifact_warnings(artifact_checks))
    warnings.extend(item["warning"] for item in review_evidence if item.get("warning"))

    confidence = _confidence(
        git_payload=git_payload,
        jsonl_evidence=jsonl_evidence,
        artifact_checks=artifact_checks,
        review_evidence=review_evidence,
    )
    payload = {
        "forensics": {
            "hints": hints.to_dict(),
            "query": query_text,
            "around_window_minutes": AROUND_WINDOW_MINUTES if around_dt is not None else None,
            "confidence": confidence["label"],
            "confidence_reasons": confidence["reasons"],
        },
        "source_plan": source_plan,
        "git": git_payload,
        "jsonl_evidence": jsonl_evidence,
        "artifact_checks": [item.to_dict() for item in artifact_checks],
        "review_evidence": review_evidence,
        "warnings": sorted(set(warnings)),
        "blind_spots": sorted(set(jsonl_result.get("blind_spots", []) + plan.blind_spots)),
    }
    workflow = build_forensics_workflow(
        git_payload=git_payload,
        jsonl_evidence=jsonl_evidence,
        artifact_checks=[item.to_dict() for item in artifact_checks],
        review_evidence=review_evidence,
        confidence_label=confidence["label"],
    )
    payload["forensics"].update(workflow)
    if "masking" in jsonl_result:
        payload["masking"] = jsonl_result["masking"]
    return payload


def build_forensics_workflow(
    *,
    git_payload: dict,
    jsonl_evidence: dict,
    artifact_checks: list[dict],
    review_evidence: list[dict],
    confidence_label: str,
) -> dict:
    phases = _phase_timeline(
        git_payload=git_payload,
        jsonl_evidence=jsonl_evidence,
        artifact_checks=artifact_checks,
        review_evidence=review_evidence,
        confidence_label=confidence_label,
    )
    review_gate = _review_gate(git_payload=git_payload, review_evidence=review_evidence)
    joins = _claim_action_review_joins(phases=phases, review_gate=review_gate)
    diagnostics = _workflow_diagnostics(
        phases=phases,
        joins=joins,
        review_gate=review_gate,
        artifact_checks=artifact_checks,
        review_evidence=review_evidence,
    )
    provenance_lifecycle = _provenance_lifecycle_summary(
        git_payload=git_payload,
        phases=phases,
        joins=joins,
        review_gate=review_gate,
        artifact_checks=artifact_checks,
        review_evidence=review_evidence,
    )
    return {
        "phase_timeline": phases,
        "claim_action_review_joins": joins,
        "review_gate": review_gate,
        "diagnostics": diagnostics,
        "provenance_lifecycle": provenance_lifecycle,
    }


def _phase_timeline(
    *,
    git_payload: dict,
    jsonl_evidence: dict,
    artifact_checks: list[dict],
    review_evidence: list[dict],
    confidence_label: str,
) -> list[dict]:
    rows: list[dict] = []

    for group_index, group in enumerate(jsonl_evidence.get("groups", []), 1):
        for match_index, match in enumerate(group.get("matches", []), 1):
            action = (match.get("claim_action") or {}).get("action") or UNSUPPORTED
            rows.append(
                _phase_row(
                    phase_kind=_phase_kind_for_match(match, action),
                    order_key=_order_key(
                        match.get("timestamp"),
                        "jsonl",
                        group_index,
                        match_index,
                        int(match.get("line") or 0),
                    ),
                    timestamp_utc=match.get("timestamp"),
                    source_kind="jsonl",
                    source_ref={
                        "path": match.get("path") or group.get("path"),
                        "line": match.get("line"),
                    },
                    evidence_kind="claim" if action in {CLAIMED, UNSUPPORTED} else "tool_action",
                    claim_action=action,
                    review_verdict=None,
                    confidence=match.get("confidence") or confidence_label,
                    uncertainty=_match_uncertainty(match, action),
                    snippet=match.get("snippet"),
                    artifact_refs=match.get("artifacts") or [],
                )
            )

    commit = git_payload.get("commit")
    if isinstance(commit, dict) and commit.get("commit"):
        changed_files = commit.get("changed_files") or []
        if changed_files:
            for index, changed in enumerate(changed_files, 1):
                rows.append(
                    _phase_row(
                        phase_kind="change",
                        order_key=_order_key(commit.get("commit_time"), "git", index, 0, 0),
                        timestamp_utc=commit.get("commit_time"),
                        source_kind="git",
                        source_ref={
                            "commit": commit.get("commit"),
                            "path": changed.get("path"),
                            "status": changed.get("status"),
                        },
                        evidence_kind="code_change",
                        claim_action=CHANGED,
                        review_verdict=None,
                        confidence="high",
                        uncertainty=[],
                    )
                )
        else:
            rows.append(
                _phase_row(
                    phase_kind="change",
                    order_key=_order_key(commit.get("commit_time"), "git", 0, 0, 0),
                    timestamp_utc=commit.get("commit_time"),
                    source_kind="git",
                    source_ref={"commit": commit.get("commit")},
                    evidence_kind="code_change",
                    claim_action=CHANGED,
                    review_verdict=None,
                    confidence="high",
                    uncertainty=["commit_has_no_changed_file_list"],
                )
            )

    path_change = git_payload.get("path_last_change")
    if isinstance(path_change, dict) and path_change.get("commit") and not _commit_rows_present(rows, path_change.get("commit")):
        rows.append(
            _phase_row(
                phase_kind="change",
                order_key=_order_key(path_change.get("commit_time"), "git-path", 0, 0, 0),
                timestamp_utc=path_change.get("commit_time"),
                source_kind="git",
                source_ref={"commit": path_change.get("commit"), "path": path_change.get("path")},
                evidence_kind="code_change",
                claim_action=CHANGED,
                review_verdict=None,
                confidence="medium",
                uncertainty=["path_last_change_without_explicit_commit_hint"],
            )
        )

    for index, item in enumerate(artifact_checks, 1):
        if not item.get("exists"):
            continue
        rows.append(
            _phase_row(
                phase_kind="change",
                order_key=_order_key(None, "artifact", index, 0, 0),
                timestamp_utc=None,
                source_kind="artifact",
                source_ref={"path": item.get("path")},
                evidence_kind="artifact",
                claim_action=CHANGED,
                review_verdict=None,
                confidence="medium" if item.get("git_tracked") else "low",
                uncertainty=[] if item.get("git_tracked") else ["artifact_exists_but_git_tracking_unknown_or_false"],
            )
        )

    for index, item in enumerate(review_evidence, 1):
        verdict = item.get("review_verdict") or {}
        action = verdict.get("action") or (item.get("claim_action") or {}).get("action") or UNSUPPORTED
        rows.append(
            _phase_row(
                phase_kind="review",
                order_key=_order_key(_review_timestamp(item), "review", index, 0, 0),
                timestamp_utc=_review_timestamp(item),
                source_kind="review",
                source_ref={"path": item.get("path")},
                evidence_kind="review_verdict" if verdict else "claim",
                claim_action=action,
                review_verdict=verdict or None,
                confidence="high" if verdict else "medium",
                uncertainty=[] if verdict else ["review_artifact_without_explicit_verdict"],
            )
        )

    rows.sort(key=lambda row: row["order_key"])
    for index, row in enumerate(rows, 1):
        row["phase_id"] = f"phase-{index:03d}"
    return rows


def _phase_row(
    *,
    phase_kind: str,
    order_key: str,
    timestamp_utc: str | None,
    source_kind: str,
    source_ref: dict,
    evidence_kind: str,
    claim_action: str,
    review_verdict: dict | None,
    confidence: str,
    uncertainty: list[str],
    snippet: str | None = None,
    artifact_refs: list[str] | None = None,
) -> dict:
    row = {
        "phase_id": "",
        "phase_kind": phase_kind,
        "order_key": order_key,
        "timestamp_utc": timestamp_utc,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "evidence_kind": evidence_kind,
        "claim_action": claim_action,
        "review_verdict": review_verdict,
        "confidence": confidence,
        "uncertainty": uncertainty,
    }
    if snippet is not None:
        row["snippet"] = snippet
    if artifact_refs:
        row["artifact_refs"] = _dedupe_preserve_order([str(item) for item in artifact_refs])
    return row


def _phase_kind_for_match(match: dict, action: str) -> str:
    role = str(match.get("role") or "").lower()
    snippet = str(match.get("snippet") or "").lower()
    if role == "user":
        return "request"
    if action in {VERIFIED, PARTIALLY_VERIFIED, FAILED, BLOCKED, MERGED}:
        return "review"
    if action == CHANGED:
        return "action"
    if action == RUNNING or any(word in snippet for word in ("plan", "will", "going to", "next i")):
        return "plan"
    if any(word in snippet for word in ("failed", "error", "bug", "regression", "symptom", "wrong")):
        return "symptom"
    return "inference" if action == UNSUPPORTED else "action"


def _match_uncertainty(match: dict, action: str) -> list[str]:
    uncertainty: list[str] = []
    if not match.get("timestamp"):
        uncertainty.append("timestamp_missing")
    if action == UNSUPPORTED:
        uncertainty.append("unsupported_claim_no_action_signal")
    if not match.get("artifacts") and action in {CLAIMED, CHANGED, VERIFIED}:
        uncertainty.append("no_artifact_path_in_jsonl_match")
    return uncertainty


def _order_key(timestamp: str | None, source_kind: str, primary: int, secondary: int, line: int) -> str:
    stamp = timestamp or "9999-12-31T23:59:59+00:00"
    return f"{stamp}|{source_kind}|{primary:04d}|{secondary:04d}|{line:08d}"


def _commit_rows_present(rows: list[dict], commit: str | None) -> bool:
    if not commit:
        return False
    return any((row.get("source_ref") or {}).get("commit") == commit for row in rows)


def _review_timestamp(item: dict) -> str | None:
    change = item.get("git_last_change") or {}
    return change.get("commit_time") if isinstance(change, dict) else None


def _review_gate(*, git_payload: dict, review_evidence: list[dict]) -> dict:
    verdicts = [
        item.get("review_verdict")
        for item in review_evidence
        if isinstance(item.get("review_verdict"), dict)
    ]
    actions = [item.get("action") for item in verdicts if item.get("action")]
    if VERIFIED in actions:
        status = "verified"
        verdict = VERIFIED
        confidence = "high"
    elif MERGED in actions:
        status = "merged"
        verdict = MERGED
        confidence = "high"
    elif any(action in {FAILED, BLOCKED} for action in actions):
        status = "blocked"
        verdict = next(action for action in actions if action in {FAILED, BLOCKED})
        confidence = "high"
    elif PARTIALLY_VERIFIED in actions:
        status = "partial"
        verdict = PARTIALLY_VERIFIED
        confidence = "medium"
    else:
        status = "unknown"
        verdict = None
        confidence = "low"
    blockers = [
        {"path": item.get("path"), "verdict": (item.get("review_verdict") or {}).get("action")}
        for item in review_evidence
        if (item.get("review_verdict") or {}).get("action") in {FAILED, BLOCKED, PARTIALLY_VERIFIED}
    ]
    commit = git_payload.get("commit") if isinstance(git_payload.get("commit"), dict) else {}
    commit_action = classify_claim_action(" ".join(str(commit.get(key) or "") for key in ("subject", "body"))).action
    return {
        "status": status,
        "verdict": verdict,
        "gate_source_refs": [{"path": item.get("path")} for item in review_evidence if item.get("path")],
        "blockers": blockers,
        "merge_commit": commit.get("commit") if commit_action == MERGED else None,
        "confidence": confidence,
        "uncertainty": [] if verdicts else ["review_verdict_not_found"],
    }


def _claim_action_review_joins(*, phases: list[dict], review_gate: dict) -> list[dict]:
    joins: list[dict] = []
    claim_rows = [row for row in phases if row.get("evidence_kind") == "claim" and row.get("claim_action") != UNSUPPORTED]
    unsupported_rows = [row for row in phases if row.get("claim_action") == UNSUPPORTED]
    action_rows = [row for row in phases if row.get("evidence_kind") in {"tool_action", "code_change", "artifact"}]
    review_rows = [row for row in phases if row.get("phase_kind") == "review"]
    change_rows = [row for row in phases if row.get("evidence_kind") in {"code_change", "artifact"}]

    for claim in claim_rows:
        for action in action_rows[:4]:
            if claim["phase_id"] == action["phase_id"]:
                continue
            joins.append(_join(claim, action, relation="supports", support_level="indirect"))

    for action in action_rows:
        for change in change_rows[:4]:
            if action["phase_id"] == change["phase_id"]:
                continue
            joins.append(_join(action, change, relation="supports", support_level="direct"))

    for review in review_rows:
        if review.get("claim_action") in {VERIFIED, MERGED}:
            targets = change_rows or action_rows or claim_rows
            for target in targets[:4]:
                if review["phase_id"] != target["phase_id"]:
                    joins.append(_join(review, target, relation="verifies", support_level="direct"))
        elif review.get("claim_action") in {FAILED, BLOCKED, PARTIALLY_VERIFIED}:
            targets = claim_rows or action_rows
            for target in targets[:4]:
                relation = "contradicts" if target.get("claim_action") in {CLAIMED, VERIFIED} else "blocks"
                joins.append(_join(review, target, relation=relation, support_level="direct"))

    for before, after in zip(phases, phases[1:]):
        joins.append(_join(before, after, relation="precedes", support_level="direct", confidence="high"))

    return _dedupe_joins(joins)


def _join(
    source: dict,
    target: dict,
    *,
    relation: str,
    support_level: str,
    confidence: str | None = None,
) -> dict:
    uncertainty = []
    if support_level != "direct":
        uncertainty.append("join_inferred_from_correlated_forensics_bundle")
    basis = _join_basis(source, target, relation=relation, support_level=support_level)
    uncertainty.extend(item for item in basis.get("uncertainty", []) if item not in uncertainty)
    return {
        "from_id": source["phase_id"],
        "to_id": target["phase_id"],
        "relation": relation,
        "support_level": support_level,
        "basis_kind": basis["basis_kind"],
        "join_basis": basis,
        "source_refs": [source.get("source_ref"), target.get("source_ref")],
        "confidence": confidence or basis.get("confidence") or ("high" if support_level == "direct" else "medium"),
        "uncertainty": uncertainty,
    }


def _join_basis(source: dict, target: dict, *, relation: str, support_level: str) -> dict:
    if relation == "precedes":
        if source.get("timestamp_utc") and target.get("timestamp_utc"):
            return {
                "basis_kind": "timestamp_only",
                "basis_label": "timestamp-only",
                "matched_fields": ["timestamp_order"],
                "explanation": "source precedes target only by phase timestamp ordering",
                "confidence": "medium",
                "uncertainty": ["timestamp_order_only_no_shared_artifact_or_commit"],
            }
        return {
            "basis_kind": "inferred",
            "basis_label": "inferred",
            "matched_fields": [],
            "explanation": "phase ordering uses fallback ordering because one or both timestamps are missing",
            "confidence": "low",
            "uncertainty": ["timestamp_missing_or_fallback_order_key"],
        }
    matched_fields = _matched_join_fields(source, target)
    if matched_fields:
        return {
            "basis_kind": "exact_direct",
            "basis_label": "exact/direct",
            "matched_fields": matched_fields,
            "explanation": "source and target share an explicit path, artifact, or commit reference",
            "confidence": "high",
            "uncertainty": [],
        }
    if support_level == "direct":
        return {
            "basis_kind": "direct",
            "basis_label": "direct",
            "matched_fields": [],
            "explanation": "join comes from direct forensics phase relation without a shared explicit ref",
            "confidence": "medium",
            "uncertainty": ["direct_phase_relation_without_shared_ref"],
        }
    if support_level == "indirect":
        return {
            "basis_kind": "indirect_bundle",
            "basis_label": "indirect/bundle",
            "matched_fields": [],
            "explanation": "join is inferred from the bounded forensics evidence bundle",
            "confidence": "medium",
            "uncertainty": ["join_inferred_from_correlated_forensics_bundle"],
        }
    return {
        "basis_kind": "inferred",
        "basis_label": "inferred",
        "matched_fields": [],
        "explanation": "join basis is inferred from available phase context",
        "confidence": "low",
        "uncertainty": ["join_basis_inferred"],
    }


def _matched_join_fields(source: dict, target: dict) -> list[str]:
    source_refs = _join_ref_values(source)
    target_refs = _join_ref_values(target)
    matches = sorted(source_refs & target_refs)
    return matches[:8]


def _join_ref_values(row: dict) -> set[str]:
    refs: set[str] = set()
    source_ref = row.get("source_ref") or {}
    if isinstance(source_ref, dict):
        for key in ("path", "commit"):
            value = source_ref.get(key)
            if value:
                refs.add(f"{key}:{value}")
    for artifact in row.get("artifact_refs") or []:
        refs.add(f"path:{artifact}")
    return refs


def _dedupe_joins(joins: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict] = []
    for join in joins:
        key = (join["from_id"], join["to_id"], join["relation"])
        if key in seen:
            continue
        seen.add(key)
        result.append(join)
    return result


def _provenance_lifecycle_summary(
    *,
    git_payload: dict,
    phases: list[dict],
    joins: list[dict],
    review_gate: dict,
    artifact_checks: list[dict],
    review_evidence: list[dict],
) -> dict:
    lifecycle = git_payload.get("lifecycle") if isinstance(git_payload.get("lifecycle"), dict) else {}
    branch = git_payload.get("branch") if isinstance(git_payload.get("branch"), dict) else {}
    commit = git_payload.get("commit") if isinstance(git_payload.get("commit"), dict) else {}
    path_change = git_payload.get("path_last_change") if isinstance(git_payload.get("path_last_change"), dict) else {}
    worktree = git_payload.get("worktree") if isinstance(git_payload.get("worktree"), dict) else {}
    lifecycle_commits = list(lifecycle.get("commits") or [])
    task_refs = [item.get("path") for item in review_evidence if _provenance_path_kind(item.get("path")) == "task"]
    review_refs = [item.get("path") for item in review_evidence if _provenance_path_kind(item.get("path")) == "review"]
    artifact_refs = [item.get("path") for item in artifact_checks if item.get("path")]
    existing_artifact_refs = [item.get("path") for item in artifact_checks if item.get("exists")]
    tracked_artifact_refs = [item.get("path") for item in artifact_checks if item.get("git_tracked")]
    basis_counts: dict[str, int] = {}
    for join in joins:
        key = str(join.get("basis_kind") or "unknown")
        basis_counts[key] = basis_counts.get(key, 0) + 1
    phase_source_counts: dict[str, int] = {}
    for phase in phases:
        key = str(phase.get("source_kind") or "unknown")
        phase_source_counts[key] = phase_source_counts.get(key, 0) + 1

    commit_refs = _dedupe_preserve_order(
        [
            str(item)
            for item in [
                *(entry.get("commit") for entry in lifecycle_commits if isinstance(entry, dict)),
                commit.get("commit"),
                path_change.get("commit"),
            ]
            if item
        ]
    )
    branch_refs = _dedupe_preserve_order(
        [str(item) for item in [branch.get("name"), git_payload.get("current_branch"), lifecycle.get("ref")] if item]
    )
    return {
        "schema_version": 1,
        "summary": {
            "commit_count": len(lifecycle_commits) or (1 if commit.get("commit") else 0),
            "branch_count": len([item for item in branch_refs if item]),
            "task_artifact_count": len(task_refs),
            "review_artifact_count": len(review_refs),
            "artifact_count": len(artifact_refs),
            "existing_artifact_count": len(existing_artifact_refs),
            "tracked_artifact_count": len(tracked_artifact_refs),
            "worktree_count": 1 if worktree.get("path") else 0,
            "join_count": len(joins),
            "join_basis_counts": basis_counts,
            "phase_source_counts": phase_source_counts,
            "review_gate_status": review_gate.get("status"),
        },
        "git_lifecycle": {
            "repo": git_payload.get("repo"),
            "current_branch": git_payload.get("current_branch"),
            "branch": branch or None,
            "commit": commit or None,
            "path_last_change": path_change or None,
            "commit_order": lifecycle.get("commit_order") or "newest_first",
            "max_commits": lifecycle.get("max_commits") or MAX_LIFECYCLE_COMMITS,
            "bounded": bool(lifecycle.get("bounded")),
            "commits": lifecycle_commits,
            "commit_refs": commit_refs,
            "branch_refs": branch_refs,
        },
        "task_review_artifacts": {
            "task_refs": task_refs[:12],
            "review_refs": review_refs[:12],
            "review_gate_status": review_gate.get("status"),
            "review_gate_verdict": review_gate.get("verdict"),
        },
        "artifact_provenance": {
            "artifact_refs": artifact_refs[:20],
            "existing_refs": existing_artifact_refs[:20],
            "tracked_refs": tracked_artifact_refs[:20],
        },
        "worktree": worktree or None,
    }


def _provenance_path_kind(path: str | None) -> str:
    if not path:
        return "unknown"
    normalized = str(path).replace("\\", "/")
    if normalized.startswith("tasks/"):
        return "task"
    if normalized.startswith("reviews/") or normalized.startswith("codex/state/reviews/"):
        return "review"
    return "artifact"


def _workflow_diagnostics(
    *,
    phases: list[dict],
    joins: list[dict],
    review_gate: dict,
    artifact_checks: list[dict],
    review_evidence: list[dict],
) -> dict:
    phase_counts: dict[str, int] = {}
    for row in phases:
        phase_counts[row["phase_kind"]] = phase_counts.get(row["phase_kind"], 0) + 1
    relation_counts: dict[str, int] = {}
    basis_counts: dict[str, int] = {}
    for join in joins:
        relation_counts[join["relation"]] = relation_counts.get(join["relation"], 0) + 1
        basis = str(join.get("basis_kind") or "unknown")
        basis_counts[basis] = basis_counts.get(basis, 0) + 1
    unsupported_count = sum(1 for row in phases if row.get("claim_action") == UNSUPPORTED)
    inferred_only_count = sum(1 for join in joins if join.get("support_level") == "inferred")
    indirect_count = sum(1 for join in joins if join.get("support_level") == "indirect")
    missing_flags = []
    if not any(row.get("source_kind") == "git" for row in phases):
        missing_flags.append("git_change_evidence_missing")
    if not review_evidence:
        missing_flags.append("review_evidence_missing")
    if not artifact_checks:
        missing_flags.append("artifact_evidence_missing")
    if review_gate.get("status") == "unknown":
        missing_flags.append("review_gate_unknown")
    return {
        "phase_counts": phase_counts,
        "observed_phase_count": sum(1 for row in phases if not row.get("uncertainty")),
        "inferred_or_uncertain_phase_count": sum(1 for row in phases if row.get("uncertainty")),
        "join_count": len(joins),
        "join_relation_counts": relation_counts,
        "join_basis_counts": basis_counts,
        "join_coverage": {
            "phase_count": len(phases),
            "joined_phase_count": len({join["from_id"] for join in joins} | {join["to_id"] for join in joins}),
        },
        "review_gate_status_counts": {review_gate.get("status") or "unknown": 1},
        "missing_evidence_flags": missing_flags,
        "inferred_only_count": inferred_only_count,
        "indirect_join_count": indirect_count,
        "unsupported_claim_count": unsupported_count,
    }


def _git_payload(*, cwd: Path, repo: Path | None, hints: ForensicsHints) -> tuple[dict, list[str], str | None]:
    warnings: list[str] = []
    payload: dict = {
        "repo": str(repo) if repo is not None else None,
        "current_branch": current_branch(repo) if repo is not None else None,
        "commit": None,
        "branch": None,
        "path_last_change": None,
        "worktree": None,
        "lifecycle": None,
    }
    if repo is None:
        return payload, warnings, None
    payload["worktree"] = worktree_metadata(repo)

    commit_ref = hints.commit
    if commit_ref is None and hints.path:
        change = last_change_for_path(_path_for_hint(hints.path, cwd=cwd, repo=repo), repo=repo)
        if change is not None:
            payload["path_last_change"] = change.to_dict()
            commit_ref = change.commit
    if commit_ref is not None:
        metadata = commit_metadata(repo, commit_ref)
        if metadata is None:
            warnings.append(f"Could not resolve git commit/ref: {commit_ref}")
        else:
            payload["commit"] = metadata.to_dict()
    if hints.branch:
        metadata = branch_metadata(repo, hints.branch)
        if metadata is None:
            warnings.append(f"Could not resolve git branch/ref: {hints.branch}")
        else:
            payload["branch"] = metadata
    lifecycle_ref = hints.branch or commit_ref or payload.get("current_branch")
    lifecycle_commits = commit_lifecycle(repo, lifecycle_ref, max_commits=MAX_LIFECYCLE_COMMITS) if lifecycle_ref else []
    payload["lifecycle"] = {
        "ref": lifecycle_ref,
        "commit_order": "newest_first",
        "max_commits": MAX_LIFECYCLE_COMMITS,
        "bounded": True,
        "commits": lifecycle_commits,
    }
    commit_payload = payload.get("commit") or {}
    inferred_around = commit_payload.get("commit_time") if isinstance(commit_payload, dict) else None
    return payload, warnings, inferred_around


def _path_for_hint(path: str, *, cwd: Path, repo: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return repo / candidate if (repo / candidate).exists() else cwd / candidate


def _deterministic_query(hints: ForensicsHints, git_payload: dict) -> str:
    parts: list[str] = []
    for value in (hints.task, hints.path, hints.commit, hints.branch):
        if value:
            parts.append(value)
    commit_payload = git_payload.get("commit")
    if isinstance(commit_payload, dict):
        if commit_payload.get("subject"):
            parts.append(str(commit_payload["subject"]))
        for changed in commit_payload.get("changed_files", []):
            if isinstance(changed, dict) and changed.get("path"):
                parts.append(str(changed["path"]))
    path_change = git_payload.get("path_last_change")
    if isinstance(path_change, dict) and path_change.get("subject"):
        parts.append(str(path_change["subject"]))
    return " ".join(_dedupe_preserve_order(parts))


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = " ".join(str(value).split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _apply_around_window(intent: QueryIntent, around):
    intent.date_window_start = around - timedelta(minutes=AROUND_WINDOW_MINUTES)
    intent.date_window_end = around + timedelta(minutes=AROUND_WINDOW_MINUTES)
    intent.date_reason = f"around:{around.isoformat()}"
    intent.date_window_filter = True


def _mask_context_from_args(args) -> MaskContext | None:
    if not args.mask_manifest and not args.redaction_profile:
        return None
    mask_context = (
        MaskContext.from_path(Path(args.mask_manifest).expanduser(), args.mask_mode)
        if args.mask_manifest
        else MaskContext.disabled(mode=args.mask_mode)
    )
    if args.redaction_profile:
        redaction_context = RedactionContext.from_path(Path(args.redaction_profile).expanduser())
        mask_context.redaction_context = redaction_context
        if mask_context.stats is not None:
            mask_context.stats.redaction = redaction_context.stats.to_dict()
            for warning in redaction_context.stats.warnings:
                if warning not in mask_context.stats.warnings:
                    mask_context.stats.warnings.append(warning)
    return mask_context


def _classify_jsonl_evidence(result: dict, *, cwd: Path, repo: Path | None) -> dict:
    classified_groups = []
    for group in result.get("groups", []):
        classified_matches = []
        for match in group.get("matches", []):
            snippet = str(match.get("snippet") or "")
            classification = classify_claim_action(snippet).to_dict()
            artifact_checks = inspect_artifacts(
                match.get("artifacts") or [],
                cwd=cwd,
                repo=repo,
                include_git=True,
            )
            enriched = dict(match)
            enriched["claim_action"] = classification
            enriched["artifact_checks"] = [item.to_dict() for item in artifact_checks]
            classified_matches.append(enriched)
        classified_group = dict(group)
        classified_group["matches"] = classified_matches
        classified_groups.append(classified_group)
    return {
        "searched_files": result.get("searched_files", 0),
        "searched_bytes": result.get("searched_bytes", 0),
        "groups": classified_groups,
    }


def _artifact_checks(
    *,
    hints: ForensicsHints,
    git_payload: dict,
    jsonl_evidence: dict,
    cwd: Path,
    repo: Path | None,
) -> list[ArtifactStatus]:
    paths: list[str] = []
    if hints.path:
        paths.append(hints.path)
    commit_payload = git_payload.get("commit")
    if isinstance(commit_payload, dict):
        for changed in commit_payload.get("changed_files", []):
            if isinstance(changed, dict) and changed.get("path"):
                paths.append(str(changed["path"]))
    for group in jsonl_evidence.get("groups", []):
        for match in group.get("matches", []):
            paths.extend(str(item) for item in match.get("artifacts", []))
    statuses: list[ArtifactStatus] = []
    seen: set[str] = set()
    for path in _dedupe_preserve_order(paths):
        if path in seen:
            continue
        seen.add(path)
        statuses.append(resolve_artifact_path(path, cwd=cwd, repo=repo, include_git=True))
    return statuses


def _discover_review_evidence(task: str | None, *, cwd: Path, repo: Path | None) -> list[dict]:
    if not task:
        return []
    root = repo or cwd
    candidates: list[Path] = []
    for directory in (root / "tasks", root / "reviews", root / "codex" / "state" / "reviews"):
        if not directory.is_dir():
            continue
        candidates.extend(sorted(directory.glob(f"*{task}*.md")))
    evidence: list[dict] = []
    seen: set[Path] = set()
    for path in candidates[:25]:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        rel = _relative_or_absolute(resolved, root)
        item = {
            "path": rel,
            "exists": resolved.exists(),
            "git_last_change": None,
            "claim_action": None,
            "review_verdict": None,
            "warning": None,
        }
        change = last_change_for_path(resolved, repo=root)
        if change is not None:
            item["git_last_change"] = change.to_dict()
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            item["warning"] = f"Could not read review/task artifact {rel}: {exc}"
            evidence.append(item)
            continue
        if len(content.encode("utf-8", errors="replace")) > MAX_ARTIFACT_BYTES:
            content = content[:MAX_ARTIFACT_BYTES]
            item["warning"] = f"Truncated review/task artifact classification at {MAX_ARTIFACT_BYTES} bytes: {rel}"
        verdict = parse_review_verdict(content)
        item["review_verdict"] = verdict.to_dict() if verdict is not None else None
        item["claim_action"] = classify_claim_action(content).to_dict()
        evidence.append(item)
    return evidence


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _artifact_warnings(artifact_checks: list[ArtifactStatus]) -> list[str]:
    warnings: list[str] = []
    for status in artifact_checks:
        if status.reason:
            warnings.append(f"Artifact check warning for {status.path}: {status.reason}")
    return warnings


def _confidence(*, git_payload: dict, jsonl_evidence: dict, artifact_checks: list[ArtifactStatus], review_evidence: list[dict]) -> dict:
    reasons: list[str] = []
    if git_payload.get("commit") or git_payload.get("path_last_change") or git_payload.get("branch"):
        reasons.append("git_metadata_present")
    if any(group.get("matches") for group in jsonl_evidence.get("groups", [])):
        reasons.append("jsonl_evidence_present")
    if any(status.exists for status in artifact_checks):
        reasons.append("artifact_exists")
    if review_evidence:
        reasons.append("review_or_task_artifact_present")
    count = len(reasons)
    if count >= 3:
        label = "high"
    elif count >= 2:
        label = "medium"
    else:
        label = "low"
    if not reasons:
        reasons.append("no_correlated_evidence_found")
    return {"label": label, "reasons": reasons}
