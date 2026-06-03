from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .records import parse_record
from .sources import (
    Limits,
    build_source_plan,
    cap_hits_from_warnings,
    iter_directory_jsonl_files,
    path_is_inside,
    source_kind_for_path,
    stat_size,
)


REPORT_PATH = Path("reports/jsh-060a-project-source-inventory-and-strata-design.md")

PUBLIC_SAFE_FAMILIES = {
    "geometry_wars": "geometry_wars",
    "history_mirror": "codex_history_mirror",
    "cwd_unavailable": "cwd_unavailable",
}

GENERIC_PATH_PARTS = {
    "",
    "home",
    "users",
    "user",
    "documents",
    "mnt",
    "c",
    "d",
    "worktrees",
    "claude-worktrees",
    ".git",
}


@dataclass
class InventoryBuildResult:
    source_inventory: dict[str, Any]
    strata_plan: dict[str, Any]
    committed_report: str


def build_inventory_bundle(
    *,
    cwd: Path,
    codex_home: Path,
    claude_home: Path,
    limits: Limits,
    now: datetime | None = None,
) -> InventoryBuildResult:
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    plan = build_source_plan(
        cwd=cwd,
        codex_home=codex_home,
        claude_home=claude_home,
        roots=[codex_home / "sessions", codex_home / "history.jsonl", claude_home / "projects"],
        allow_arbitrary_root=False,
        limits=limits,
    )
    file_entries, scan_metrics = collect_inventory_entries(plan, now=now, claude_home=claude_home)
    geometry_probe_entries = collect_geometry_probe_entries(file_entries, claude_home=claude_home)
    if geometry_probe_entries:
        existing_paths = {item["path"] for item in file_entries}
        for item in geometry_probe_entries:
            if item["path"] not in existing_paths:
                file_entries.append(item)
                existing_paths.add(item["path"])
        scan_metrics["warnings"].append("Added a dedicated Geometry Wars metadata probe under the approved Claude root after the main capped pass.")
    family_labels = assign_public_family_labels(file_entries)
    for item in file_entries:
        item["family_public"] = family_labels[item["family_private"]]
    for item in file_entries:
        if item["source_kind"] == "codex_history":
            item["duplicate_cluster_id"] = "mirror_pool"
            item["duplicate_risk"] = "history_mirror_pool"
        elif item["source_kind"] == "codex_sessions":
            item["duplicate_cluster_id"] = "possible_history_mirror"
            item["duplicate_risk"] = "possible_history_mirror"
        else:
            item["duplicate_cluster_id"] = "unavailable"
            item["duplicate_risk"] = "not_evaluated"

    family_rows = build_family_rows(file_entries)
    geometry_status = build_geometry_status(file_entries, family_rows)
    strata_rows = build_strata_rows(file_entries, family_rows, geometry_status)
    source_inventory = build_source_inventory_payload(
        started=started,
        now=now,
        cwd=cwd,
        codex_home=codex_home,
        claude_home=claude_home,
        limits=limits,
        plan=plan,
        scan_metrics=scan_metrics,
        file_entries=file_entries,
        family_rows=family_rows,
        geometry_status=geometry_status,
    )
    strata_plan = build_strata_plan_payload(
        now=now,
        file_entries=file_entries,
        family_rows=family_rows,
        geometry_status=geometry_status,
        strata_rows=strata_rows,
    )
    committed_report = render_committed_report(source_inventory, strata_plan)
    return InventoryBuildResult(
        source_inventory=source_inventory,
        strata_plan=strata_plan,
        committed_report=committed_report,
    )


def build_file_entry(path: Path, entry: dict[str, Any], *, now: datetime, claude_home: Path) -> dict[str, Any]:
    relationship = relationship_hint(path, entry["source_kind"])
    family_private = private_family_for_entry(path, entry, claude_home=claude_home)
    reference_dt = parse_iso(entry.get("date_end")) or parse_iso(entry.get("date_start"))
    if reference_dt is None and entry.get("mtime"):
        reference_dt = datetime.fromtimestamp(entry["mtime"], tz=timezone.utc)
    recency_bucket = recency_bucket_for(reference_dt, now=now)
    size_bucket = size_bucket_for(int(entry.get("record_count") or 0), int(entry.get("size") or 0))
    density_bucket = density_bucket_for(int(entry.get("record_count") or 0), int(entry.get("size") or 0))
    meta = load_claude_sidecar_meta(path) if relationship == "claude_subagent" else None
    session_private = session_family_private(path, entry["source_kind"], relationship)
    cwd_families = sorted(
        {
            family
            for family in (
                normalize_cwd_family(cwd_hint)
                for cwd_hint in (entry.get("cwd_hints") or [])
            )
            if family and family != "cwd_unavailable"
        }
    )
    time_span_days = time_span_day_count(entry.get("date_start"), entry.get("date_end"))
    return {
        "source_id": private_source_id(path),
        "path": str(path),
        "source_kind": entry["source_kind"],
        "framework": entry.get("framework") or infer_framework(entry["source_kind"]),
        "family_private": family_private,
        "family_public": "",
        "family_support": family_support_for(entry["source_kind"], family_private),
        "relationship_hint": relationship,
        "session_family_private": session_private,
        "size_bytes": entry["size"],
        "line_count": entry["line_count"],
        "record_count": entry["record_count"],
        "record_hash": None,
        "content_hash": None,
        "date_start": entry.get("date_start"),
        "date_end": entry.get("date_end"),
        "mtime_utc": datetime.fromtimestamp(entry["mtime"], tz=timezone.utc).isoformat() if entry.get("mtime") else None,
        "recency_bucket": recency_bucket,
        "size_bucket": size_bucket,
        "density_bucket": density_bucket,
        "large_conversation": size_bucket in {"large", "huge"},
        "time_span_days": time_span_days,
        "cwd_hint_count": len(entry.get("cwd_hints") or []),
        "cwd_family_candidates_private": cwd_families[:8],
        "task_hint_count": 0,
        "artifact_hint_count": 0,
        "sidecar_meta": sanitize_sidecar_meta(meta),
    }


def build_source_inventory_payload(
    *,
    started: float,
    now: datetime,
    cwd: Path,
    codex_home: Path,
    claude_home: Path,
    limits: Limits,
    plan,
    scan_metrics: dict[str, Any],
    file_entries: list[dict[str, Any]],
    family_rows: list[dict[str, Any]],
    geometry_status: dict[str, Any],
) -> dict[str, Any]:
    warnings = sorted(set(list(plan.warnings) + list(scan_metrics.get("warnings") or [])))
    source_counts = Counter(item["source_kind"] for item in file_entries)
    density_counts = Counter(item["density_bucket"] for item in file_entries)
    recency_counts = Counter(item["recency_bucket"] for item in file_entries)
    relationship_counts = Counter(item["relationship_hint"] for item in file_entries)
    size_counts = Counter(item["size_bucket"] for item in file_entries)
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    return {
        "schema_version": 1,
        "task": "jsh-060a-project-source-inventory-and-strata-design",
        "generated_at": now.isoformat(),
        "approved_roots": {
            "cwd": str(cwd),
            "codex_home": str(codex_home),
            "claude_home": str(claude_home),
            "selected_roots": [root.to_dict() for root in plan.roots],
            "rejected_roots": [root.to_dict() for root in plan.rejected_roots],
            "blind_spots": list(plan.blind_spots),
        },
        "limits": limits.to_dict(),
        "scan_metrics": {
            "file_count": len(file_entries),
            "total_size_bytes": sum(item["size_bytes"] for item in file_entries),
            "total_line_count": sum(item["line_count"] for item in file_entries),
            "total_record_count": sum(item["record_count"] for item in file_entries),
            "manifest_elapsed_ms": "unavailable",
            "inventory_elapsed_ms": elapsed_ms,
            "manifest_bytes_parsed": scan_metrics.get("bytes_read"),
            "cap_hits": cap_hits_from_warnings(warnings),
            "warnings": warnings,
            "warnings_count": len(warnings),
            "sidecar_meta_used": sum(1 for item in file_entries if item["sidecar_meta"]["present"]),
        },
        "group_counts": {
            "by_source_kind": dict(source_counts),
            "by_density_bucket": dict(density_counts),
            "by_recency_bucket": dict(recency_counts),
            "by_relationship_hint": dict(relationship_counts),
            "by_size_bucket": dict(size_counts),
        },
        "family_groups": family_rows,
        "geometry_wars": geometry_status,
        "blind_spots": build_inventory_blind_spots(file_entries, geometry_status, warnings),
        "file_entries": file_entries,
    }


def build_strata_plan_payload(
    *,
    now: datetime,
    file_entries: list[dict[str, Any]],
    family_rows: list[dict[str, Any]],
    geometry_status: dict[str, Any],
    strata_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    batch_groups = defaultdict(list)
    for row in strata_rows:
        batch_groups[row["recommended_batch"]].append(row["stratum_id"])
    return {
        "schema_version": 1,
        "task": "jsh-060a-project-source-inventory-and-strata-design",
        "generated_at": now.isoformat(),
        "private_fields": [
            "path",
            "family_private",
            "session_family_private",
            "source_id",
            "cwd_family_candidates_private",
            "sidecar_meta.description_hash",
        ],
        "committed_safe_fields": [
            "family_public",
            "source_kind",
            "density_bucket",
            "recency_bucket",
            "aggregate counts",
            "geometry_wars status",
            "batch recommendations",
            "cap notes",
            "residual uncertainty",
        ],
        "family_map": [
            {
                "family_public": row["family_public"],
                "family_private": row["family_private"],
                "source_kinds": row["source_kinds"],
            }
            for row in family_rows
        ],
        "strata": strata_rows,
        "batch_recommendations": {
            "jsh-060c": {
                "focus": "large and dense project strata first",
                "candidate_id_range": {"start": "060C-0001", "end": "060C-0750"},
                "caps": {
                    "max_files": 220,
                    "max_bytes": 280_000_000,
                    "max_matches": 24,
                    "max_elapsed": 120,
                },
                "stop_conditions": [
                    "Stop after 60 mined candidates or 3 consecutive strata with only rejected or duplicate-only leads.",
                    "Stop immediately if elapsed or byte caps hit before opening the intended dense files; revise strata ordering instead of broadening roots.",
                    "Keep `codex_history_mirror` as reference-only unless a later reviewer explicitly needs a mirror confirmation row.",
                ],
                "strata_ids": batch_groups.get("jsh-060c", []),
            },
            "jsh-060d": {
                "focus": "small, old, sparse, and hard-to-locate strata",
                "candidate_id_range": {"start": "060D-1001", "end": "060D-1750"},
                "caps": {
                    "max_files": 320,
                    "max_bytes": 180_000_000,
                    "max_matches": 18,
                    "max_elapsed": 90,
                },
                "stop_conditions": [
                    "Stop after 50 mined candidates or 4 consecutive no-evidence strata.",
                    "Reserve at least one probe for the oldest usable Geometry Wars stratum when present.",
                    "Do not let small/sparse probes consume more than one pass through any stratum before reviewer feedback.",
                ],
                "strata_ids": batch_groups.get("jsh-060d", []),
            },
        },
        "geometry_wars": geometry_status,
        "residual_uncertainty": [
            "Codex history is a mirror-style aggregate source and cannot safely stand in for project-scoped primary evidence on its own.",
            "Codex session family inference depends on available cwd hints; missing cwd metadata stays `cwd_unavailable`, not zero.",
            "Claude subagent descriptions are only sampled from adjacent `.meta.json` files when present under the approved root.",
        ],
        "inventory_totals": {
            "file_count": len(file_entries),
            "family_count": len(family_rows),
            "large_or_dense_files": sum(1 for item in file_entries if item["density_bucket"] == "dense" or item["large_conversation"]),
            "small_or_sparse_files": sum(1 for item in file_entries if item["density_bucket"] == "sparse" or item["size_bucket"] == "small"),
        },
    }


def build_family_rows(file_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in file_entries:
        grouped[item["family_private"]].append(item)

    rows = []
    for family_private, items in sorted(grouped.items()):
        source_kinds = sorted({item["source_kind"] for item in items})
        recency_counts = Counter(item["recency_bucket"] for item in items)
        density_counts = Counter(item["density_bucket"] for item in items)
        size_counts = Counter(item["size_bucket"] for item in items)
        relationship_counts = Counter(item["relationship_hint"] for item in items)
        rows.append(
            {
                "family_private": family_private,
                "family_public": "",
                "source_kinds": source_kinds,
                "file_count": len(items),
                "record_count": sum(item["record_count"] for item in items),
                "size_bytes": sum(item["size_bytes"] for item in items),
                "large_file_count": sum(1 for item in items if item["large_conversation"]),
                "dense_file_count": sum(1 for item in items if item["density_bucket"] == "dense"),
                "sparse_file_count": sum(1 for item in items if item["density_bucket"] == "sparse"),
                "old_file_count": sum(1 for item in items if item["recency_bucket"] in {"aging", "old"}),
                "recent_file_count": sum(1 for item in items if item["recency_bucket"] == "recent"),
                "recency_counts": dict(recency_counts),
                "density_counts": dict(density_counts),
                "size_counts": dict(size_counts),
                "relationship_counts": dict(relationship_counts),
                "supported_by": sorted({item["family_support"] for item in items}),
                "private_source_ids": [item["source_id"] for item in items],
            }
        )
    family_labels = assign_public_family_labels(file_entries)
    for row in rows:
        row["family_public"] = family_labels[row["family_private"]]
    rows.sort(key=lambda row: (-row["record_count"], -row["file_count"], row["family_public"]))
    return rows


def build_geometry_status(file_entries: list[dict[str, Any]], family_rows: list[dict[str, Any]]) -> dict[str, Any]:
    family = next((row for row in family_rows if row["family_private"] == "geometry_wars"), None)
    if not family:
        return {
            "present": False,
            "usable": False,
            "blind_spot": "Geometry Wars family was not detected under the approved roots or could not be normalized safely.",
        }
    geometry_entries = [item for item in file_entries if item["family_private"] == "geometry_wars"]
    oldest = min((item["date_start"] or item["date_end"] or item["mtime_utc"] for item in geometry_entries if item["date_start"] or item["date_end"] or item["mtime_utc"]), default=None)
    return {
        "present": True,
        "usable": bool(geometry_entries),
        "family_public": family["family_public"],
        "file_count": len(geometry_entries),
        "old_or_aging_files": sum(1 for item in geometry_entries if item["recency_bucket"] in {"aging", "old"}),
        "source_kinds": sorted({item["source_kind"] for item in geometry_entries}),
        "oldest_observed_ref": oldest,
        "blind_spot": None if geometry_entries else "Geometry Wars was normalized but no usable files remained under current caps.",
    }


def build_strata_rows(
    file_entries: list[dict[str, Any]],
    family_rows: list[dict[str, Any]],
    geometry_status: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    index = 1
    for family in family_rows:
        family_public = family["family_public"]
        items = [item for item in file_entries if item["family_private"] == family["family_private"]]
        if family_public == "codex_history_mirror":
            rows.append(
                build_stratum_row(
                    index=index,
                    family=family,
                    items=items,
                    label="reference_mirror",
                    batch="reference_only",
                    focus="mirror_reference_only",
                    notes=["Aggregate mirror source; keep for confirmation and blind-spot accounting, not first-pass mining."],
                )
            )
            index += 1
            continue
        dense_items = [item for item in items if item["density_bucket"] == "dense" or item["large_conversation"]]
        sparse_old_items = [item for item in items if item["density_bucket"] == "sparse" or item["recency_bucket"] in {"aging", "old"}]
        if dense_items:
            rows.append(
                build_stratum_row(
                    index=index,
                    family=family,
                    items=dense_items,
                    label="dense_or_large",
                    batch="jsh-060c",
                    focus="large_dense_priority",
                    notes=["Dense or large conversation subset."],
                )
            )
            index += 1
        if sparse_old_items:
            label = "geometry_wars_old_probe" if family_public == "geometry_wars" and geometry_status.get("present") else "sparse_old_probe"
            notes = ["Reserve for small, old, or sparse mining."] if label != "geometry_wars_old_probe" else ["Dedicated Geometry Wars old-project probe when reviewer asks for hard-to-locate history."]
            rows.append(
                build_stratum_row(
                    index=index,
                    family=family,
                    items=sparse_old_items,
                    label=label,
                    batch="jsh-060d",
                    focus="small_old_sparse_priority",
                    notes=notes,
                )
            )
            index += 1
    rows.sort(key=lambda row: (batch_sort_key(row["recommended_batch"]), -row["record_count"], row["stratum_id"]))
    return rows


def build_stratum_row(
    *,
    index: int,
    family: dict[str, Any],
    items: list[dict[str, Any]],
    label: str,
    batch: str,
    focus: str,
    notes: list[str],
) -> dict[str, Any]:
    recency_counts = Counter(item["recency_bucket"] for item in items)
    density_counts = Counter(item["density_bucket"] for item in items)
    return {
        "stratum_id": f"stratum_{index:03d}",
        "family_public": family["family_public"],
        "family_private": family["family_private"],
        "recommended_batch": batch,
        "focus": focus,
        "label": label,
        "source_kinds": sorted({item["source_kind"] for item in items}),
        "file_count": len(items),
        "record_count": sum(item["record_count"] for item in items),
        "size_bytes": sum(item["size_bytes"] for item in items),
        "large_file_count": sum(1 for item in items if item["large_conversation"]),
        "dense_file_count": sum(1 for item in items if item["density_bucket"] == "dense"),
        "sparse_file_count": sum(1 for item in items if item["density_bucket"] == "sparse"),
        "recency_counts": dict(recency_counts),
        "density_counts": dict(density_counts),
        "private_source_ids": [item["source_id"] for item in items],
        "notes": notes,
    }


def render_committed_report(source_inventory: dict[str, Any], strata_plan: dict[str, Any]) -> str:
    lines = [
        "# jsh-060a Project Source Inventory And Strata Design",
        "",
        "## Scope",
        "",
        "- This pass stayed inventory-and-strata only. It did not mine or promote gold examples.",
        "- Approved roots only: Codex sessions, Codex history mirror, and Claude projects.",
        "- Exact private refs remain under ignored `.private/jsh-060/jsh-060a/`; this report is aggregate and sanitized.",
        "",
        "## Discovery Summary",
        "",
    ]
    metrics = source_inventory["scan_metrics"]
    lines.extend(
        [
            f"- Files inventoried: `{metrics['file_count']}`",
            f"- Approximate bytes covered: `{metrics['total_size_bytes']}`",
            f"- Approximate records covered: `{metrics['total_record_count']}`",
            f"- Sidecar metadata used: `{metrics['sidecar_meta_used']}` Claude subagent files",
            f"- Cap hits: `{json.dumps(metrics['cap_hits'], sort_keys=True)}`",
            "",
            "| Source kind | Files |",
            "| --- | ---: |",
        ]
    )
    for kind, count in sorted(source_inventory["group_counts"]["by_source_kind"].items()):
        lines.append(f"| `{kind}` | {count} |")

    lines.extend(
        [
            "",
            "## Family And Strata Overview",
            "",
            "| Family label | Source kinds | Files | Large | Dense | Sparse | Old/Aging | Recent |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in strata_plan["family_map"]:
        family_private = row["family_private"]
        family_public = row["family_public"]
        family_stats = next(item for item in source_inventory["family_groups"] if item["family_private"] == family_private)
        lines.append(
            f"| `{family_public}` | {', '.join(f'`{kind}`' for kind in row['source_kinds'])} | "
            f"{family_stats['file_count']} | {family_stats['large_file_count']} | {family_stats['dense_file_count']} | "
            f"{family_stats['sparse_file_count']} | {family_stats['old_file_count']} | {family_stats['recent_file_count']} |"
        )

    geometry = source_inventory["geometry_wars"]
    lines.extend(["", "## Geometry Wars", ""])
    if geometry.get("present"):
        lines.append(
            f"- Present: yes. Files: `{geometry['file_count']}`. Old/Aging files: `{geometry['old_or_aging_files']}`. "
            f"Source kinds: {', '.join(f'`{kind}`' for kind in geometry['source_kinds'])}."
        )
        geometry_probe = next(
            (
                row
                for row in strata_plan["strata"]
                if row["family_public"] == "geometry_wars" and row["recommended_batch"] == "jsh-060d"
            ),
            None,
        )
        if geometry_probe:
            lines.append(
                f"- `jsh-060d` includes dedicated Geometry Wars probe `{geometry_probe['stratum_id']}` "
                f"(`{geometry_probe['label']}`) so the old-project family is still sampled even when the visible files are recent."
            )
        if geometry.get("blind_spot"):
            lines.append(f"- Blind spot note: {geometry['blind_spot']}")
    else:
        lines.append(f"- Blind spot: {geometry['blind_spot']}")

    lines.extend(
        [
            "",
            "## Recommended Batches",
            "",
            "### jsh-060c",
            "",
            "- Focus: large and dense strata first.",
            f"- Candidate IDs: `{strata_plan['batch_recommendations']['jsh-060c']['candidate_id_range']['start']}` to "
            f"`{strata_plan['batch_recommendations']['jsh-060c']['candidate_id_range']['end']}`",
            f"- Strata: {', '.join(f'`{item}`' for item in strata_plan['batch_recommendations']['jsh-060c']['strata_ids']) or '`none`'}",
            f"- Caps: `{json.dumps(strata_plan['batch_recommendations']['jsh-060c']['caps'], sort_keys=True)}`",
        ]
    )
    for note in strata_plan["batch_recommendations"]["jsh-060c"]["stop_conditions"]:
        lines.append(f"- Stop condition: {note}")

    lines.extend(
        [
            "",
            "### jsh-060d",
            "",
            "- Focus: small, old, sparse, and hard-to-locate strata.",
            f"- Candidate IDs: `{strata_plan['batch_recommendations']['jsh-060d']['candidate_id_range']['start']}` to "
            f"`{strata_plan['batch_recommendations']['jsh-060d']['candidate_id_range']['end']}`",
            f"- Strata: {', '.join(f'`{item}`' for item in strata_plan['batch_recommendations']['jsh-060d']['strata_ids']) or '`none`'}",
            f"- Caps: `{json.dumps(strata_plan['batch_recommendations']['jsh-060d']['caps'], sort_keys=True)}`",
        ]
    )
    for note in strata_plan["batch_recommendations"]["jsh-060d"]["stop_conditions"]:
        lines.append(f"- Stop condition: {note}")

    lines.extend(["", "## Residual Uncertainty", ""])
    for note in source_inventory["blind_spots"] + strata_plan["residual_uncertainty"]:
        lines.append(f"- {note}")

    lines.extend(
        [
            "",
            "## Learning Ledger",
            "",
            "| Field | Value |",
            "| --- | --- |",
            "| `learning_id` | `jsh-060a-source-inventory-strata-plan` |",
            "| `task_or_run` | `jsh-060a-project-source-inventory-and-strata-design` |",
            "| `outcome_class` | `claim_limited` |",
            "| `method_family` | `source_inventory_and_strata_design` |",
            "| `measured_counters` | aggregate file, byte, record, density, recency, and source-kind counts; missing counters remain `unavailable` |",
            "| `cap_or_contamination_notes` | inventory caps preserved; Codex history mirror kept separate from project-scoped primary strata |",
            "| `decision_changed` | `jsh-060c` should start with dense strata and `jsh-060d` should reserve old/sparse and Geometry Wars probes |",
            f"| `docs_or_tasks_changed` | `{REPORT_PATH.as_posix()}`, task progress, scratch notes, focused helper/test surfaces |",
            "| `follow_up_created_or_rejected` | `none` |",
            "| `residual_uncertainty` | project-family inference for some Codex sessions depends on cwd hints; mirror sources remain non-primary |",
            "| `review_status` | `self_checked` |",
            "| `privacy_layer` | `committed_safe` |",
        ]
    )
    lines.append("")
    return "\n".join(lines)


def assign_public_family_labels(file_entries: list[dict[str, Any]]) -> dict[str, str]:
    families = sorted({item["family_private"] for item in file_entries})
    labels: dict[str, str] = {}
    next_index = 1
    for family in families:
        if family in PUBLIC_SAFE_FAMILIES:
            labels[family] = PUBLIC_SAFE_FAMILIES[family]
            continue
        labels[family] = f"family_{next_index:03d}"
        next_index += 1
    return labels


def build_inventory_blind_spots(file_entries: list[dict[str, Any]], geometry_status: dict[str, Any], warnings: list[str]) -> list[str]:
    blind_spots = []
    if any(item["family_private"] == "cwd_unavailable" for item in file_entries):
        blind_spots.append("Some Codex session files had no usable cwd hint, so project-family grouping remains partial for those sessions.")
    if geometry_status.get("blind_spot"):
        blind_spots.append(geometry_status["blind_spot"])
    if any("max elapsed cap" in warning.lower() for warning in warnings):
        blind_spots.append("Elapsed caps may have truncated the inventory before every approved file was parsed.")
    if any("max file cap" in warning.lower() for warning in warnings):
        blind_spots.append("File caps may have truncated the inventory before every approved file was parsed.")
    if any("max byte cap" in warning.lower() for warning in warnings):
        blind_spots.append("Byte caps may have truncated the inventory before every approved file was parsed.")
    return blind_spots


def relationship_hint(path: Path, source_kind: str) -> str:
    path_text = path.as_posix()
    if source_kind == "codex_history":
        return "history_mirror"
    if source_kind == "claude_projects" and "/subagents/" in path_text:
        return "claude_subagent"
    if source_kind == "claude_projects":
        return "claude_parent_session"
    if source_kind == "codex_sessions":
        return "codex_session"
    return "unknown"


def private_family_for_entry(path: Path, entry: dict[str, Any], *, claude_home: Path) -> str:
    if entry["source_kind"] == "codex_history":
        return "history_mirror"
    if entry["source_kind"] == "claude_projects":
        family = normalize_claude_project_family(path, claude_home=claude_home)
        return family or "cwd_unavailable"
    cwd_families = [normalize_cwd_family(cwd_hint) for cwd_hint in (entry.get("cwd_hints") or [])]
    cwd_families = [family for family in cwd_families if family and family != "cwd_unavailable"]
    if cwd_families:
        counts = Counter(cwd_families)
        return counts.most_common(1)[0][0]
    return "cwd_unavailable"


def family_support_for(source_kind: str, family_private: str) -> str:
    if source_kind == "codex_history":
        return "history_mirror"
    if family_private == "cwd_unavailable":
        return "unavailable"
    if source_kind == "claude_projects":
        return "project_path"
    return "cwd_hint"


def normalize_claude_project_family(path: Path, *, claude_home: Path) -> str:
    try:
        first = path.relative_to(claude_home / "projects").parts[0]
    except ValueError:
        return "cwd_unavailable"
    prefix = first.split("--claude-worktrees-", 1)[0]
    prefix = prefix.lstrip("-")
    marker = "projects-"
    if marker in prefix:
        prefix = prefix.split(marker, 1)[1]
    return normalize_family_slug(prefix)


def normalize_cwd_family(cwd_hint: str | None) -> str:
    if not cwd_hint:
        return "cwd_unavailable"
    normalized = cwd_hint.replace("\\", "/").strip()
    lowered = normalized.lower()
    for marker in ("projects/", "projects/"):
        index = lowered.find(marker)
        if index >= 0:
            suffix = normalized[index + len(marker) :]
            family = suffix.split("/", 1)[0]
            return normalize_family_slug(family)
    parts = [part for part in normalized.split("/") if part]
    filtered = [part for part in parts if part.lower() not in GENERIC_PATH_PARTS]
    if "worktrees" in [part.lower() for part in filtered]:
        filtered = [part for part in filtered if part.lower() != "worktrees"]
    if filtered:
        return normalize_family_slug(filtered[-1])
    return "cwd_unavailable"


def normalize_family_slug(value: str) -> str:
    lowered = value.strip().lower().replace(" ", "-")
    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
    if not tokens:
        return "cwd_unavailable"
    return "_".join(tokens)


def session_family_private(path: Path, source_kind: str, relationship: str) -> str:
    if source_kind == "codex_history":
        return "codex_history"
    if relationship == "claude_subagent":
        return path.parent.parent.name
    if relationship == "claude_parent_session":
        return path.stem
    return path.stem


def load_claude_sidecar_meta(path: Path) -> dict[str, Any] | None:
    if path.suffix != ".jsonl":
        return None
    meta_path = path.with_suffix(".meta.json")
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def sanitize_sidecar_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not meta:
        return {"present": False, "agent_type": None, "description_hash": None}
    description = str(meta.get("description") or "")
    return {
        "present": True,
        "agent_type": str(meta.get("agentType") or "") or None,
        "description_hash": digest_text(description) if description else None,
    }


def recency_bucket_for(reference_dt: datetime | None, *, now: datetime) -> str:
    if reference_dt is None:
        return "unavailable"
    delta_days = max(0, int((now - reference_dt).total_seconds() // 86400))
    if delta_days <= 30:
        return "recent"
    if delta_days <= 120:
        return "active"
    if delta_days <= 365:
        return "aging"
    return "old"


def size_bucket_for(record_count: int, size_bytes: int) -> str:
    if record_count >= 1200 or size_bytes >= 2_000_000:
        return "huge"
    if record_count >= 300 or size_bytes >= 500_000:
        return "large"
    if record_count >= 80 or size_bytes >= 120_000:
        return "medium"
    return "small"


def density_bucket_for(record_count: int, size_bytes: int) -> str:
    if record_count >= 300 or size_bytes >= 500_000:
        return "dense"
    if record_count <= 40 and size_bytes <= 60_000:
        return "sparse"
    return "mixed"


def time_span_day_count(date_start: str | None, date_end: str | None) -> int | str:
    start = parse_iso(date_start)
    end = parse_iso(date_end)
    if not start or not end:
        return "unavailable"
    return max(0, int((end - start).total_seconds() // 86400))


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def private_source_id(path: Path) -> str:
    return f"src_{digest_text(str(path))[:12]}"


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def batch_sort_key(batch: str) -> int:
    if batch == "jsh-060c":
        return 0
    if batch == "jsh-060d":
        return 1
    return 2


def collect_inventory_entries(plan, *, now: datetime, claude_home: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.monotonic()
    warnings = list(plan.warnings)
    files_seen = 0
    bytes_seen = 0
    bytes_read = 0
    file_entries: list[dict[str, Any]] = []
    for path, size, warnings in enumerate_inventory_files(plan, started=started, warnings=warnings):
        files_seen += 1
        bytes_seen += size
        probed, read_bytes = quick_probe_file(path, source_kind=source_kind_for_path(path, plan))
        bytes_read += read_bytes
        file_entries.append(build_file_entry(path, probed, now=now, claude_home=claude_home))
    metrics = {
        "files_seen": files_seen,
        "bytes_seen": bytes_seen,
        "bytes_read": bytes_read,
        "warnings": warnings,
    }
    return file_entries, metrics


def enumerate_inventory_files(plan, *, started: float, warnings: list[str]):
    roots = []
    for root in plan.roots:
        base = root.path.expanduser()
        if base.is_file():
            candidates = [base] if base.suffix in {".jsonl", ".json"} else []
        elif base.is_dir():
            candidates = iter_directory_jsonl_files(base, plan, started, warnings)
        else:
            candidates = []
        roots.append({"base": base, "iterator": iter(candidates)})

    files_seen = 0
    bytes_seen = 0
    while roots:
        advanced = False
        for slot in list(roots):
            if time.monotonic() - started > plan.limits.max_elapsed:
                warnings.append("Stopped enumeration at max elapsed cap.")
                return
            try:
                path = next(slot["iterator"])
            except StopIteration:
                roots.remove(slot)
                continue
            advanced = True
            base = slot["base"]
            if files_seen >= plan.limits.effective_files():
                warnings.append("Stopped enumeration at max file cap.")
                return
            if not path_is_inside(path, base if base.is_dir() else base.parent):
                warnings.append(f"Skipped symlink escape: {path}")
                continue
            size = stat_size(path)
            if bytes_seen + size > plan.limits.effective_bytes():
                warnings.append("Stopped enumeration at max byte cap.")
                return
            files_seen += 1
            bytes_seen += size
            yield path, size, warnings
        if not advanced:
            break


def quick_probe_file(path: Path, *, source_kind: str) -> tuple[dict[str, Any], int]:
    line_count = 0
    head_lines: list[str] = []
    bytes_read = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line_count += 1
                bytes_read += len(line.encode("utf-8", errors="replace"))
                if len(head_lines) < 12:
                    head_lines.append(line)
    except OSError:
        pass

    cwd_hints: list[str] = []
    timestamps: list[datetime] = []
    for line_number, raw in enumerate(head_lines, 1):
        record = parse_record(raw, path=path, line=line_number, default_tz=timezone.utc)
        if record.cwd:
            cwd_hints.append(record.cwd)
        if record.timestamp is not None:
            timestamps.append(record.timestamp)

    return (
        {
            "path": str(path),
            "source_kind": source_kind,
            "framework": infer_framework(source_kind),
            "size": stat_size(path),
            "mtime": path.stat().st_mtime if path.exists() else 0.0,
            "line_count": line_count,
            "record_count": line_count,
            "date_start": min(timestamps).isoformat() if timestamps else None,
            "date_end": max(timestamps).isoformat() if timestamps else None,
            "cwd_hints": sorted(set(cwd_hints))[:8],
        },
        bytes_read,
    )


def infer_framework(source_kind: str) -> str:
    if source_kind.startswith("codex"):
        return "codex"
    if source_kind.startswith("claude"):
        return "claude"
    return "generic_jsonl"


def collect_geometry_probe_entries(file_entries: list[dict[str, Any]], *, claude_home: Path) -> list[dict[str, Any]]:
    if any(item["family_private"] == "geometry_wars" for item in file_entries):
        return []
    root = claude_home / "projects"
    if not root.exists():
        return []
    candidate_dirs = sorted(
        path for path in root.iterdir() if path.is_dir() and "geometry-wars" in path.name.lower()
    )
    if not candidate_dirs:
        return []
    selected_files: list[Path] = []
    bytes_seen = 0
    for directory in candidate_dirs:
        files = sorted(directory.rglob("*.jsonl"), key=lambda item: item.stat().st_mtime if item.exists() else 0.0)
        for path in files:
            size = stat_size(path)
            if bytes_seen + size > 50_000_000:
                return geometry_probe_file_entries(selected_files, claude_home=claude_home)
            selected_files.append(path)
            bytes_seen += size
            if len(selected_files) >= 24:
                return geometry_probe_file_entries(selected_files, claude_home=claude_home)
    return geometry_probe_file_entries(selected_files, claude_home=claude_home)


def geometry_probe_file_entries(paths: list[Path], *, claude_home: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for path in paths:
        probed, _bytes_read = quick_probe_file(path, source_kind="claude_projects")
        items.append(build_file_entry(path, probed, now=now, claude_home=claude_home))
    return items
