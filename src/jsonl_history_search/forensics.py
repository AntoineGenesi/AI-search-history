from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

from .artifacts import ArtifactStatus, inspect_artifacts, resolve_artifact_path
from .claims import classify_claim_action, parse_review_verdict
from .gittools import branch_metadata, commit_metadata, current_branch, git_root_for, last_change_for_path
from .masking import MaskContext
from .query import QueryIntent, infer_query
from .redaction import RedactionContext
from .scanner import search
from .sources import Limits, build_source_plan
from .timeutil import parse_datetime, parse_now, timezone_from_args


AROUND_WINDOW_MINUTES = 30
MAX_ARTIFACT_BYTES = 200_000


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
    if "masking" in jsonl_result:
        payload["masking"] = jsonl_result["masking"]
    return payload


def _git_payload(*, cwd: Path, repo: Path | None, hints: ForensicsHints) -> tuple[dict, list[str], str | None]:
    warnings: list[str] = []
    payload: dict = {
        "repo": str(repo) if repo is not None else None,
        "current_branch": current_branch(repo) if repo is not None else None,
        "commit": None,
        "branch": None,
        "path_last_change": None,
    }
    if repo is None:
        return payload, warnings, None

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
