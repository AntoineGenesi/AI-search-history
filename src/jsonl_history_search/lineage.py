from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .clustering import task_slugs_from_text
from .gittools import current_branch, git_root_for
from .masking import MaskContext
from .query import infer_query
from .redaction import RedactionContext
from .scanner import search
from .sources import Limits, build_source_plan
from .timeutil import parse_now, timezone_from_args


DEFAULT_MAX_SIBLING_DIRS = 40
DEFAULT_MAX_PROJECT_FILES = 250
LINEAGE_WORDS = {
    "ancestor",
    "based",
    "copied",
    "derived",
    "extracted",
    "fork",
    "lineage",
    "original",
    "renamed",
    "sibling",
    "source",
}


@dataclass(frozen=True)
class ProjectFingerprint:
    path: str
    name: str
    exists: bool
    git_root: str | None = None
    git_branch: str | None = None
    package_names: list[str] = field(default_factory=list)
    task_slugs: list[str] = field(default_factory=list)
    file_sample: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def build_lineage_payload(args) -> dict:
    tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, tz)
    cwd = Path(args.cwd).expanduser().resolve()
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"
    claude_home = Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude"
    project_root = Path(args.project_root).expanduser().resolve() if args.project_root else cwd
    sibling_roots = [Path(item).expanduser().resolve() for item in args.sibling_root] if args.sibling_root else [project_root.parent]
    limits = Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=args.max_matches,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files,
        hard_cap_bytes=args.hard_cap_bytes,
    )

    target = fingerprint_project(project_root, max_files=args.max_project_files)
    siblings, discovery_warnings = discover_sibling_projects(
        project_root,
        sibling_roots=sibling_roots,
        max_dirs=args.max_sibling_dirs,
        max_files=args.max_project_files,
        target=target,
    )
    query_text = deterministic_lineage_query(target, siblings, args.query)
    intent = infer_query(query_text, now=now)
    plan = build_source_plan(
        cwd=project_root,
        codex_home=codex_home,
        claude_home=claude_home,
        roots=[Path(item).expanduser() for item in args.root] if args.root else None,
        allow_arbitrary_root=args.allow_arbitrary_root,
        limits=limits,
    )
    source_plan = {
        "query": intent.to_dict(),
        "sources": plan.to_dict(),
        "cwd": str(project_root),
        "codex_home": str(codex_home),
        "claude_home": str(claude_home),
        "now": now.isoformat(),
    }
    result = search(
        plan,
        intent,
        default_tz=tz,
        cwd=str(project_root),
        mask_context=_mask_context_from_args(args),
    )
    warnings = sorted(set(discovery_warnings + result.get("warnings", []) + intent.warnings + plan.warnings))
    timeline = lineage_timeline(result)
    candidates = rank_candidate_projects(
        target,
        siblings,
        timeline,
        max_common_files=args.max_common_files,
        max_projects=args.max_projects,
    )
    hypotheses = relationship_hypotheses(target, candidates, timeline)
    payload = {
        "project_lineage": {
            "schema_version": 1,
            "status": "candidates_found" if candidates or timeline else "no_evidence",
            "cwd": str(cwd),
            "query": query_text,
            "subject_project": target.to_dict(),
            "candidate_projects": [candidate for candidate in candidates],
            "relationship_hypotheses": hypotheses,
            "timeline": timeline[: args.timeline_limit],
            "compact_ai_handoff": compact_ai_handoff(target, candidates, hypotheses, timeline),
            "next_read": next_read(candidates, timeline),
            "speed_accuracy_contract": {
                "sibling_discovery": "immediate directories under configured sibling roots only",
                "sibling_ordering": "hint-score first, then newest directory mtime, then name before applying the cap",
                "max_sibling_dirs": args.max_sibling_dirs,
                "max_project_files_per_candidate": args.max_project_files,
                "jsonl_limits": limits.to_dict(),
                "timestamp_ordering": "parsed message timestamp ascending in UTC, then path and line",
                "accuracy_rule": "folder/file overlap is only a candidate signal; transcript/git evidence is required for stronger lineage claims",
            },
        },
        "source_plan": source_plan,
        "warnings": warnings,
        "blind_spots": sorted(set(result.get("blind_spots", []) + plan.blind_spots)),
    }
    if "masking" in result:
        payload["masking"] = result["masking"]
    return payload


def fingerprint_project(path: Path, *, max_files: int) -> ProjectFingerprint:
    warnings: list[str] = []
    exists = path.exists()
    git_root = git_root_for(path)
    git_branch = current_branch(git_root) if git_root is not None else None
    package_names = package_names_for(path)
    task_slugs = task_slugs_for(path)
    file_sample = file_sample_for(path, max_files=max_files) if exists and path.is_dir() else []
    if not exists:
        warnings.append(f"Project path does not exist: {path}")
    return ProjectFingerprint(
        path=str(path),
        name=path.name,
        exists=exists,
        git_root=str(git_root) if git_root is not None else None,
        git_branch=git_branch,
        package_names=package_names,
        task_slugs=task_slugs,
        file_sample=file_sample,
        warnings=warnings,
    )


def discover_sibling_projects(
    project_root: Path,
    *,
    sibling_roots: list[Path],
    max_dirs: int,
    max_files: int,
    target: ProjectFingerprint | None = None,
) -> tuple[list[ProjectFingerprint], list[str]]:
    warnings: list[str] = []
    candidates: list[ProjectFingerprint] = []
    seen: set[Path] = {project_root.resolve(strict=False)}
    for root in sibling_roots:
        if not root.exists() or not root.is_dir():
            warnings.append(f"Missing sibling root: {root}")
            continue
        try:
            children = sorted(
                (child for child in root.iterdir() if child.is_dir()),
                key=lambda item: sibling_discovery_key(item, project_root=project_root, target=target),
            )
        except OSError as exc:
            warnings.append(f"Could not enumerate sibling root {root}: {exc}")
            continue
        for child in children:
            resolved = child.resolve(strict=False)
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append(fingerprint_project(child, max_files=max_files))
            if len(candidates) >= max_dirs:
                warnings.append("Stopped sibling discovery at max sibling directory cap.")
                return candidates, warnings
    return candidates, warnings


def sibling_discovery_key(child: Path, *, project_root: Path, target: ProjectFingerprint | None) -> tuple[float, float, str]:
    hints = project_hint_tokens(project_root, target)
    child_name = child.name.lower()
    child_tokens = tokenise_hint_text(child_name)
    overlap = len(hints & child_tokens)
    substring_hits = sum(1 for hint in hints if len(hint) >= 4 and hint in child_name)
    lineage_hits = len(child_tokens & LINEAGE_WORDS)
    try:
        mtime = child.stat().st_mtime
    except OSError:
        mtime = 0.0
    hint_score = (overlap * 4) + (substring_hits * 2) + lineage_hits
    return (-float(hint_score), -mtime, child_name)


def project_hint_tokens(project_root: Path, target: ProjectFingerprint | None) -> set[str]:
    hints = set(tokenise_hint_text(project_root.name))
    if target is not None:
        for value in [target.name, *target.package_names, *target.task_slugs[:20]]:
            hints.update(tokenise_hint_text(value))
    return {item for item in hints if len(item) >= 3}


def tokenise_hint_text(value: str) -> set[str]:
    return {item for item in re.split(r"[^a-z0-9]+", value.lower()) if item}


def package_names_for(path: Path) -> list[str]:
    names: set[str] = set()
    pyproject = path / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for pattern in (r"(?m)^\s*name\s*=\s*['\"]([^'\"]+)['\"]", r"(?m)^\s*packages\s*=\s*\[([^\]]+)\]"):
            for match in re.findall(pattern, text):
                for name in re.findall(r"[A-Za-z0-9_.-]+", match):
                    if len(name) > 1 and name.lower() not in {"project", "tool", "poetry"}:
                        names.add(name.lower())
    package_json = path / "package.json"
    if package_json.exists():
        try:
            text = package_json.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        match = re.search(r'"name"\s*:\s*"([^"]+)"', text)
        if match:
            names.add(match.group(1).lower())
    return sorted(names)


def task_slugs_for(path: Path) -> list[str]:
    slugs: set[str] = set()
    for directory_name in ("tasks", "reviews", "codex/state/reviews"):
        directory = path / directory_name
        if not directory.is_dir():
            continue
        for item in sorted(directory.glob("*.md"))[:100]:
            slugs.update(task_slugs_from_text(item.name))
    return sorted(slugs)


def file_sample_for(path: Path, *, max_files: int) -> list[str]:
    roots = [path / name for name in ("src", "tests", "docs", "tasks", "reviews")]
    files: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for item in sorted(root.rglob("*")):
            if item.is_file():
                try:
                    files.append(item.relative_to(path).as_posix())
                except ValueError:
                    continue
                if len(files) >= max_files:
                    return files
    return files


def deterministic_lineage_query(target: ProjectFingerprint, siblings: list[ProjectFingerprint], extra: str | None) -> str:
    parts = [
        "derived source sibling lineage copied extracted renamed original",
        target.name,
        *target.package_names,
        *target.task_slugs[:8],
    ]
    for sibling in siblings[:12]:
        parts.append(sibling.name)
        parts.extend(sibling.package_names[:3])
        parts.extend(sibling.task_slugs[:3])
    if extra:
        parts.append(extra)
    return " ".join(dedupe_preserve_order(parts))


def lineage_timeline(search_result: dict) -> list[dict]:
    rows: list[dict] = []
    for group in search_result.get("groups", []):
        for match in group.get("matches", []):
            text = " ".join(str(match.get("snippet") or "").split())
            rows.append(
                {
                    "source": "jsonl",
                    "kind": "transcript_match",
                    "path": match.get("path"),
                    "line": match.get("line"),
                    "timestamp": match.get("timestamp"),
                    "timestamp_utc": timestamp_utc(match.get("timestamp")),
                    "role": match.get("role"),
                    "cwd": match.get("cwd"),
                    "session": match.get("session") or group.get("session"),
                    "snippet_or_summary": text,
                    "relationship_terms": sorted(set(re.findall(r"[a-z0-9-]+", text.lower())) & LINEAGE_WORDS),
                    "tasks": list(match.get("tasks") or []),
                    "artifacts": list(match.get("artifacts") or []),
                    "score": match.get("score"),
                    "weight": evidence_weight(match, text),
                    "reasons": list(match.get("reasons") or []),
                }
            )
    rows.sort(key=lambda item: (timestamp_sort_key(item.get("timestamp_utc")), item.get("path") or "", int(item.get("line") or 0)))
    return rows


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


def timestamp_sort_key(value: str | None) -> str:
    return value or "9999-12-31T23:59:59+00:00"


def evidence_weight(match: dict, text: str) -> float:
    weight = float(match.get("score") or 0)
    if set(re.findall(r"[a-z0-9-]+", text.lower())) & LINEAGE_WORDS:
        weight += 5
    if match.get("cwd"):
        weight += 2
    return round(weight, 3)


def rank_candidate_projects(
    target: ProjectFingerprint,
    siblings: list[ProjectFingerprint],
    timeline: list[dict],
    *,
    max_common_files: int,
    max_projects: int,
) -> list[dict]:
    target_files = set(target.file_sample)
    target_packages = set(target.package_names)
    target_tasks = set(target.task_slugs)
    ranked: list[dict] = []
    for sibling in siblings:
        reasons: list[str] = []
        score = 0.0
        common_files = sorted(target_files & set(sibling.file_sample))[:max_common_files]
        common_packages = sorted(target_packages & set(sibling.package_names))
        common_tasks = sorted(target_tasks & set(sibling.task_slugs))
        mentions = timeline_mentions_project(timeline, sibling)
        if common_packages:
            score += 18 * len(common_packages)
            reasons.extend(f"common_package:{name}" for name in common_packages)
        if common_tasks:
            score += 14 * len(common_tasks)
            reasons.extend(f"common_task:{slug}" for slug in common_tasks[:8])
        if common_files:
            score += min(30, 3 * len(common_files))
            reasons.append(f"common_files:{len(common_files)}")
        if sibling.git_root and target.git_root and sibling.git_root == target.git_root:
            score += 12
            reasons.append("same_git_root")
        if mentions:
            score += 10 * len(mentions)
            reasons.append(f"jsonl_mentions:{len(mentions)}")
        candidate = {
            "candidate_id": "",
            "path": sibling.path,
            "name": sibling.name,
            "score": round(score, 3),
            "relationship_hypothesis": relationship_label(score, mentions, common_packages, common_tasks, common_files),
            "confidence": confidence_label(score, mentions),
            "reason_codes": reason_codes_for(reasons),
            "supporting_evidence": supporting_evidence_for(reasons, mentions, common_files),
            "contrary_evidence": [],
            "uncertainty": [],
            "common_package_names": common_packages,
            "common_task_slugs": common_tasks[:20],
            "common_files": common_files,
            "jsonl_evidence_refs": [
                {"path": item["path"], "line": item["line"], "timestamp": item["timestamp"]}
                for item in mentions[:5]
            ],
            "git_root": sibling.git_root,
        }
        candidate["contrary_evidence"] = contrary_evidence(target, candidate)
        candidate["uncertainty"] = [uncertainty_for(candidate, timeline)]
        ranked.append(candidate)
    ranked.sort(key=lambda item: (-item["score"], item["name"].lower()))
    positive = [item for item in ranked if item["score"] > 0][:max_projects]
    for index, item in enumerate(positive, 1):
        item["candidate_id"] = f"p{index:03d}"
    return positive


def reason_codes_for(reasons: list[str]) -> list[str]:
    codes: set[str] = set()
    for reason in reasons:
        key = reason.split(":", 1)[0]
        if key == "common_package":
            codes.add("package_name_overlap")
        elif key == "common_task":
            codes.add("task_slug_overlap")
        elif key == "common_files":
            codes.add("common_file_overlap")
        elif key == "same_git_root":
            codes.add("same_git_root")
        elif key == "jsonl_mentions":
            codes.add("jsonl_project_mention")
        else:
            codes.add(key)
    return sorted(codes)


def supporting_evidence_for(reasons: list[str], mentions: list[dict], common_files: list[str]) -> list[dict]:
    evidence: list[dict] = []
    for reason in reasons[:12]:
        evidence.append({"source": "project_metadata", "kind": reason.split(":", 1)[0], "summary": reason})
    for item in mentions[:5]:
        evidence.append(
            {
                "source": "jsonl",
                "kind": "transcript_match",
                "timestamp": item.get("timestamp"),
                "timestamp_utc": item.get("timestamp_utc"),
                "path": item.get("path"),
                "line": item.get("line"),
                "snippet_or_summary": item.get("snippet_or_summary"),
                "weight": item.get("weight"),
            }
        )
    for path in common_files[:5]:
        evidence.append({"source": "filesystem", "kind": "common_file", "snippet_or_summary": path, "weight": 1})
    return evidence[:20]


def timeline_mentions_project(timeline: list[dict], project: ProjectFingerprint) -> list[dict]:
    needles = {project.name.lower(), *(name.lower() for name in project.package_names)}
    return [
        item
        for item in timeline
        if any(needle and needle in " ".join([str(item.get("snippet_or_summary") or ""), str(item.get("cwd") or "")]).lower() for needle in needles)
    ]


def relationship_label(
    score: float,
    mentions: list[dict],
    common_packages: list[str],
    common_tasks: list[str],
    common_files: list[str],
) -> str:
    mention_terms = set().union(*(set(item.get("relationship_terms") or []) for item in mentions)) if mentions else set()
    if mention_terms & {"source", "original", "ancestor"}:
        return "possible_source_or_ancestor"
    if mention_terms & {"sibling", "derived", "copied", "extracted", "renamed"}:
        return "possible_sibling_or_derived_project"
    if common_packages or common_tasks:
        return "likely_related_project"
    if len(common_files) >= 5 or score >= 10:
        return "weak_file_overlap_candidate"
    return "weak_candidate"


def confidence_label(score: float, mentions: list[dict]) -> str:
    if score >= 40 and mentions:
        return "high"
    if score >= 18:
        return "medium"
    if score > 0:
        return "low"
    return "weak"


def relationship_hypotheses(target: ProjectFingerprint, candidates: list[dict], timeline: list[dict]) -> list[dict]:
    hypotheses: list[dict] = []
    for candidate in candidates[:8]:
        hypotheses.append(
            {
                "candidate": candidate["name"],
                "relationship": candidate["relationship_hypothesis"],
                "confidence": candidate["confidence"],
                "supporting_evidence": candidate["supporting_evidence"][:8],
                "contrary_evidence": candidate["contrary_evidence"],
                "uncertainty": candidate["uncertainty"],
            }
        )
    return hypotheses


def contrary_evidence(target: ProjectFingerprint, candidate: dict) -> list[str]:
    contrary: list[str] = []
    if target.git_root and candidate.get("git_root") and target.git_root != candidate.get("git_root"):
        contrary.append("different_git_roots")
    if not candidate.get("jsonl_evidence_refs"):
        contrary.append("no_direct_jsonl_mentions")
    if not candidate.get("common_package_names") and not candidate.get("common_task_slugs"):
        contrary.append("no_package_or_task_slug_overlap")
    return contrary


def uncertainty_for(candidate: dict, timeline: list[dict]) -> str:
    if candidate["confidence"] == "high":
        return "Strong candidate, but inspect the referenced timeline rows before asserting lineage."
    if not timeline:
        return "No transcript evidence survived the bounded source plan; this is filesystem/git metadata only."
    return "Candidate needs source-row inspection; overlap and mentions are not proof of derivation order."


def compact_ai_handoff(
    target: ProjectFingerprint,
    candidates: list[dict],
    hypotheses: list[dict],
    timeline: list[dict],
) -> dict:
    return {
        "target": {"name": target.name, "path": target.path, "packages": target.package_names, "git_root": target.git_root},
        "top_candidates": [
            {
                "name": item["name"],
                "relationship": item["relationship_hypothesis"],
                "confidence": item["confidence"],
                "score": item["score"],
                "reason_codes": item["reason_codes"][:5],
            }
            for item in candidates[:8]
        ],
        "timeline_refs": [
            {"path": item["path"], "line": item["line"], "timestamp": item["timestamp"], "cwd": item["cwd"]}
            for item in timeline[:10]
        ],
        "hypotheses": hypotheses[:8],
        "recommended_next_read": next_read(candidates, timeline),
    }


def next_read(candidates: list[dict], timeline: list[dict]) -> list[str]:
    suggestions = [
        "Open the top candidate timeline refs before explaining lineage.",
        "Prefer timestamped JSONL mentions over folder-name/file-overlap signals.",
        "If no direct transcript mention exists, report the answer as a candidate relationship, not a proven derivation.",
    ]
    if candidates:
        suggestions.append(f"Inspect candidate {candidates[0]['candidate_id']} ({candidates[0]['name']}) first.")
    if timeline:
        first = timeline[0]
        suggestions.append(f"Start timeline inspection at {first.get('path')}:{first.get('line')}.")
    return suggestions


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = " ".join(str(value).split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


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
