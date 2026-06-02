from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

from .adapters import AdapterReadError, iter_records_for_file
from .manifest import content_hash_for_path, record_hash_for_path
from .sources import (
    Limits,
    SourcePlan,
    build_source_plan,
    cap_hits_from_warnings,
    enumerate_jsonl_files,
    path_hash,
    root_label_for_path,
    safe_resolve,
    source_kind_for_path,
    stat_mtime,
    stat_size,
)


SCHEMA_VERSION = 1
FIXTURE_MARKER = ".jsonl-archive-synthetic-fixture"


@dataclass
class SnapshotEntry:
    source_id: str
    source_label: str
    source_kind: str
    source_path: str
    source_path_hash: str
    archive_object_id: str
    archive_relpath: str
    source_bytes: int
    source_mtime: float
    source_content_hash: str
    archive_content_hash: str
    record_hash: str | None
    source_ref_count: int
    source_refs: list[dict[str, Any]]
    duplicate_group: str | None = None
    duplicate_of: str | None = None

    def to_ledger_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_label": self.source_label,
            "source_kind": self.source_kind,
            "source_path": self.source_path,
            "source_path_hash": self.source_path_hash,
            "archive_object_id": self.archive_object_id,
            "archive_relpath": self.archive_relpath,
            "source_bytes": self.source_bytes,
            "source_mtime": self.source_mtime,
            "source_content_hash": self.source_content_hash,
            "archive_content_hash": self.archive_content_hash,
            "record_hash": self.record_hash,
            "source_ref_count": self.source_ref_count,
            "source_refs": self.source_refs,
            "duplicate_group": self.duplicate_group,
            "duplicate_of": self.duplicate_of,
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_label": self.source_label,
            "source_kind": self.source_kind,
            "source_path_hash": self.source_path_hash,
            "archive_object_id": self.archive_object_id,
            "source_bytes": self.source_bytes,
            "source_content_hash": self.source_content_hash,
            "archive_content_hash": self.archive_content_hash,
            "record_hash": self.record_hash,
            "source_ref_count": self.source_ref_count,
            "source_refs": self.source_refs,
            "duplicate_group": self.duplicate_group,
            "duplicate_of": self.duplicate_of,
        }


def build_archive_snapshot_proof_payload(args) -> dict[str, Any]:
    started = time.monotonic()
    errors = _validate_args(args)
    if errors:
        return _invalid_payload(errors=errors, started=started, args=args)

    cwd = Path(args.cwd).expanduser().resolve()
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"
    claude_home = Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude"
    limits = Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=0,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files,
        hard_cap_bytes=args.hard_cap_bytes,
    )
    roots = [Path(item).expanduser() for item in args.root]
    plan = build_source_plan(
        cwd=cwd,
        codex_home=codex_home,
        claude_home=claude_home,
        roots=roots,
        allow_arbitrary_root=args.allow_arbitrary_root,
        limits=limits,
    )
    planning_errors = _planning_errors(plan, roots=roots)
    archive_output_dir = Path(args.archive_output_dir).expanduser()
    export_output_dir = Path(args.export_output_dir).expanduser()
    output_errors = _validate_output_dirs(
        archive_output_dir=archive_output_dir,
        export_output_dir=export_output_dir,
        plan=plan,
    )
    if planning_errors or output_errors:
        return _invalid_payload(errors=planning_errors + output_errors, started=started, args=args, plan=plan)

    archive_output_dir.mkdir(parents=True, exist_ok=True)
    export_output_dir.mkdir(parents=True, exist_ok=True)
    objects_dir = archive_output_dir / "objects"
    objects_dir.mkdir(parents=True, exist_ok=True)

    entries, warnings = _write_snapshot_entries(plan, objects_dir=objects_dir)
    _apply_duplicate_metadata(entries)
    ledger = _ledger_payload(entries, warnings=warnings, limits=limits)
    manifest = _manifest_payload(entries, warnings=warnings, limits=limits)
    ledger_path = archive_output_dir / "ledger.json"
    manifest_path = archive_output_dir / "manifest.json"
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    verification = verify_ledger_statuses(ledger_path=ledger_path)
    export = export_snapshot(ledger_path=ledger_path, export_output_dir=export_output_dir, default_tz=timezone.utc)
    status_probes = _run_status_probes(archive_output_dir=archive_output_dir) if args.include_status_probes else []
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    gate = _archive_gate_result(entries, verification=verification, export=export, status_probes=status_probes)
    return {
        "archive_snapshot_proof": {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if gate["synthetic_proof_passed"] else "proof_failed",
            "scope": "committed_synthetic_fixture_only",
            "local_only": True,
            "background_behavior": False,
            "default_archive": False,
            "archive_search_index": False,
            "encryption": False,
            "promotion_ready": False,
            "source_plan": _public_source_plan(plan),
            "limits": limits.to_dict(),
            "snapshot": {
                "source_file_count": len(entries),
                "source_bytes": sum(entry.source_bytes for entry in entries),
                "bytes_copied": sum(entry.source_bytes for entry in entries),
                "bytes_compressed": 0,
                "storage_format": "raw_byte_copy_no_compression",
                "manifest_bytes": stat_size(manifest_path),
                "ledger_bytes": stat_size(ledger_path),
                "index_bytes": stat_size(manifest_path) + stat_size(ledger_path),
                "archive_object_count": len({entry.archive_object_id for entry in entries}),
                "elapsed_ms": elapsed_ms,
            },
            "manifest": _public_manifest(manifest),
            "ledger": _public_ledger(entries),
            "duplicate_detection": _duplicate_detection(entries),
            "hash_verification": verification,
            "export_restore_smoke": export,
            "status_handling": {
                "status_probes_enabled": bool(args.include_status_probes),
                "probes": status_probes,
                "covered_statuses": sorted({probe.get("status") for probe in status_probes}),
            },
            "archive_protection_gate": gate,
            "privacy": {
                "private_logs_used": False,
                "committed_fixture_only": True,
                "stdout_uses_raw_paths": False,
                "artifact_paths_are_explicit": True,
                "raw_transcript_text_in_payload": False,
            },
            "artifacts": {
                "archive_output_dir_label": "explicit_archive_output_dir",
                "export_output_dir_label": "explicit_export_output_dir",
                "ledger": "ledger.json",
                "manifest": "manifest.json",
            },
            "warnings": _sanitize_warnings(warnings + plan.warnings),
        }
    }


def verify_ledger_statuses(*, ledger_path: Path) -> dict[str, Any]:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    base_dir = ledger_path.parent
    statuses = []
    for raw_entry in ledger.get("entries", []):
        statuses.append(_verify_entry_status(raw_entry, base_dir=base_dir))
    counts: dict[str, int] = {}
    for status in statuses:
        key = status["status"]
        counts[key] = counts.get(key, 0) + 1
    return {
        "status": "pass" if statuses and all(item["archive_hash_matches"] for item in statuses) else "fail",
        "entry_count": len(statuses),
        "status_counts": counts,
        "entries": [_public_status(item) for item in statuses],
    }


def export_snapshot(*, ledger_path: Path, export_output_dir: Path, default_tz) -> dict[str, Any]:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    base_dir = ledger_path.parent
    export_output_dir.mkdir(parents=True, exist_ok=True)
    exports = []
    for raw_entry in ledger.get("entries", []):
        archive_path = base_dir / raw_entry["archive_relpath"]
        target = export_output_dir / f"{raw_entry['source_id']}{Path(raw_entry['source_path']).suffix or '.jsonl'}"
        if not archive_path.exists():
            exports.append(
                {
                    "source_id": raw_entry["source_id"],
                    "status": "archive_missing",
                    "source_ref_equivalence": "not_compared",
                }
            )
            continue
        shutil.copy2(archive_path, target)
        exported_hash = content_hash_for_path(target)
        exported_refs = _source_refs_for_file(
            target,
            source_kind=str(raw_entry.get("source_kind") or "arbitrary_jsonl_root"),
            source_label=str(raw_entry.get("source_label") or raw_entry["source_id"]),
            default_tz=default_tz,
        )
        expected_refs = raw_entry.get("source_refs") or []
        equivalent_refs = _source_ref_signature(expected_refs) == _source_ref_signature(exported_refs)
        exports.append(
            {
                "source_id": raw_entry["source_id"],
                "status": "restored" if exported_hash == raw_entry.get("source_content_hash") else "hash_mismatch",
                "exported_content_hash": exported_hash,
                "expected_content_hash": raw_entry.get("source_content_hash"),
                "hash_equivalence": exported_hash == raw_entry.get("source_content_hash"),
                "source_ref_equivalence": "equivalent" if equivalent_refs else "mismatch",
                "source_ref_count": len(exported_refs),
            }
        )
    return {
        "status": "pass" if exports and all(item["status"] == "restored" and item["source_ref_equivalence"] == "equivalent" for item in exports) else "fail",
        "exported_file_count": len(exports),
        "entries": exports,
    }


def _validate_args(args) -> list[str]:
    errors = []
    if not args.root:
        errors.append("archive snapshot proof requires at least one explicit --root")
    if not args.allow_arbitrary_root:
        errors.append("archive snapshot proof requires --allow-arbitrary-root for synthetic fixture roots")
    if args.hard_cap_files is None or args.hard_cap_bytes is None:
        errors.append("archive snapshot proof requires explicit --hard-cap-files and --hard-cap-bytes")
    if not args.archive_output_dir:
        errors.append("archive snapshot proof requires explicit --archive-output-dir")
    if not args.export_output_dir:
        errors.append("archive snapshot proof requires explicit --export-output-dir")
    return errors


def _planning_errors(plan: SourcePlan, *, roots: list[Path]) -> list[str]:
    errors = []
    if plan.rejected_roots:
        errors.append("one or more roots were rejected before traversal")
    if not plan.roots:
        errors.append("no selected roots were approved")
    for root in roots:
        if not _is_committed_synthetic_fixture_root(root):
            errors.append("root is outside the committed archive snapshot restore fixture or missing its synthetic marker")
    return errors


def _validate_output_dirs(*, archive_output_dir: Path, export_output_dir: Path, plan: SourcePlan) -> list[str]:
    errors = []
    archive_resolved = safe_resolve(archive_output_dir)
    export_resolved = safe_resolve(export_output_dir)
    if archive_resolved == export_resolved:
        errors.append("archive and export output directories must be different")
    for root in plan.roots:
        root_resolved = safe_resolve(root.path)
        if _path_contains(root_resolved, archive_resolved) or _path_contains(root_resolved, export_resolved):
            errors.append("archive/export output directories must not be inside selected roots")
        if _path_contains(archive_resolved, root_resolved) or _path_contains(export_resolved, root_resolved):
            errors.append("selected roots must not be inside archive/export output directories")
    return sorted(set(errors))


def _write_snapshot_entries(plan: SourcePlan, *, objects_dir: Path) -> tuple[list[SnapshotEntry], list[str]]:
    entries: list[SnapshotEntry] = []
    warnings = list(plan.warnings)
    for path, size, enum_warnings in enumerate_jsonl_files(plan):
        warnings = list(enum_warnings)
        source_kind = source_kind_for_path(path, plan)
        content_hash = content_hash_for_path(path)
        if not content_hash:
            warnings.append("Could not hash one selected source file.")
            continue
        digest = content_hash.split(":", 1)[1]
        archive_relpath = f"objects/{digest}.raw"
        archive_path = objects_dir / f"{digest}.raw"
        if not archive_path.exists():
            shutil.copy2(path, archive_path)
        archive_hash = content_hash_for_path(archive_path)
        source_id = f"source_{len(entries) + 1:03d}"
        source_label = f"{root_label_for_path(path, plan)}:{source_id}"
        entry = SnapshotEntry(
            source_id=source_id,
            source_label=source_label,
            source_kind=source_kind,
            source_path=str(path),
            source_path_hash=path_hash(path),
            archive_object_id=f"object_{digest[:16]}",
            archive_relpath=archive_relpath,
            source_bytes=size,
            source_mtime=stat_mtime(path),
            source_content_hash=content_hash,
            archive_content_hash=archive_hash or "",
            record_hash=record_hash_for_path(path, source_kind=source_kind, default_tz=timezone.utc),
            source_ref_count=0,
            source_refs=[],
        )
        entry.source_refs = _source_refs_for_file(path, source_kind=source_kind, source_label=source_label, default_tz=timezone.utc)
        entry.source_ref_count = len(entry.source_refs)
        entries.append(entry)
    return entries, warnings


def _apply_duplicate_metadata(entries: list[SnapshotEntry]) -> None:
    groups: dict[str, list[SnapshotEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.source_content_hash, []).append(entry)
    group_index = 1
    for duplicates in groups.values():
        if len(duplicates) <= 1:
            continue
        group_id = f"duplicate_group_{group_index:03d}"
        group_index += 1
        canonical = duplicates[0].source_id
        for entry in duplicates:
            entry.duplicate_group = group_id
            entry.duplicate_of = None if entry.source_id == canonical else canonical


def _ledger_payload(entries: list[SnapshotEntry], *, warnings: list[str], limits: Limits) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "committed_synthetic_fixture_only",
        "entries": [entry.to_ledger_dict() for entry in entries],
        "limits": limits.to_dict(),
        "warnings": _sanitize_warnings(warnings),
    }


def _manifest_payload(entries: list[SnapshotEntry], *, warnings: list[str], limits: Limits) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "committed_synthetic_fixture_only",
        "source_file_count": len(entries),
        "source_bytes": sum(entry.source_bytes for entry in entries),
        "archive_object_count": len({entry.archive_object_id for entry in entries}),
        "duplicate_group_count": len({entry.duplicate_group for entry in entries if entry.duplicate_group}),
        "entries": [entry.to_public_dict() for entry in entries],
        "cap_status": cap_hits_from_warnings(warnings),
        "limits": limits.to_dict(),
    }


def _public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": manifest["schema_version"],
        "scope": manifest["scope"],
        "source_file_count": manifest["source_file_count"],
        "source_bytes": manifest["source_bytes"],
        "archive_object_count": manifest["archive_object_count"],
        "duplicate_group_count": manifest["duplicate_group_count"],
        "cap_status": manifest["cap_status"],
        "entries": manifest["entries"],
    }


def _public_ledger(entries: list[SnapshotEntry]) -> dict[str, Any]:
    return {
        "entry_count": len(entries),
        "entries": [entry.to_public_dict() for entry in entries],
    }


def _duplicate_detection(entries: list[SnapshotEntry]) -> dict[str, Any]:
    groups: dict[str, list[str]] = {}
    for entry in entries:
        if entry.duplicate_group:
            groups.setdefault(entry.duplicate_group, []).append(entry.source_id)
    return {
        "status": "duplicates_detected" if groups else "no_duplicates",
        "duplicate_group_count": len(groups),
        "groups": [{"group_id": group_id, "source_ids": source_ids} for group_id, source_ids in sorted(groups.items())],
    }


def _verify_entry_status(entry: dict[str, Any], *, base_dir: Path) -> dict[str, Any]:
    archive_path = base_dir / str(entry.get("archive_relpath") or "")
    source_path = Path(str(entry.get("source_path") or ""))
    archive_hash = content_hash_for_path(archive_path) if archive_path.exists() else None
    source_exists = source_path.exists()
    source_hash = content_hash_for_path(source_path) if source_exists else None
    source_bytes = stat_size(source_path) if source_exists else None
    archive_hash_matches = archive_hash == entry.get("archive_content_hash") == entry.get("source_content_hash")
    source_hash_matches = source_hash == entry.get("source_content_hash")
    source_size_matches = source_bytes == entry.get("source_bytes") if source_exists else False
    if not archive_path.exists():
        status = "missing_archive_object"
    elif not archive_hash_matches:
        status = "corrupt_archive_object"
    elif source_exists and source_hash_matches and source_size_matches and entry.get("duplicate_of"):
        status = "duplicate_content"
    elif source_exists and source_hash_matches and source_size_matches:
        status = "protected"
    elif source_exists:
        status = "stale_source_changed"
    else:
        status = "source_deleted_but_archived"
    return {
        "source_id": entry.get("source_id"),
        "status": status,
        "source_exists": source_exists,
        "archive_object_exists": archive_path.exists(),
        "archive_hash_matches": bool(archive_hash_matches),
        "source_hash_matches": bool(source_hash_matches),
        "source_size_matches": bool(source_size_matches),
        "duplicate_of": entry.get("duplicate_of"),
    }


def _public_status(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": status.get("source_id"),
        "status": status.get("status"),
        "source_exists": status.get("source_exists"),
        "archive_object_exists": status.get("archive_object_exists"),
        "archive_hash_matches": status.get("archive_hash_matches"),
        "source_hash_matches": status.get("source_hash_matches"),
        "source_size_matches": status.get("source_size_matches"),
        "duplicate_of": status.get("duplicate_of"),
    }


def _run_status_probes(*, archive_output_dir: Path) -> list[dict[str, Any]]:
    probes_dir = archive_output_dir / "status_probes"
    probes_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        ("source_deleted", "source_deleted_but_archived"),
        ("stale", "stale_source_changed"),
        ("missing", "missing_archive_object"),
        ("corrupt", "corrupt_archive_object"),
    ]
    probes = []
    for index, (name, expected) in enumerate(specs, 1):
        source = probes_dir / f"{name}.jsonl"
        source.write_text(
            json.dumps({"timestamp": "2026-06-02T00:00:00+00:00", "role": "user", "text": f"synthetic {name} probe"}) + "\n",
            encoding="utf-8",
        )
        content_hash = content_hash_for_path(source) or ""
        digest = content_hash.split(":", 1)[1] if ":" in content_hash else hashlib.sha256(name.encode()).hexdigest()
        archive_relpath = f"status_probes/{name}-{digest[:12]}.raw"
        archive_path = archive_output_dir / archive_relpath
        shutil.copy2(source, archive_path)
        entry = {
            "source_id": f"probe_{index:03d}",
            "source_label": f"status_probe:{name}",
            "source_kind": "arbitrary_jsonl_root",
            "source_path": str(source),
            "source_path_hash": path_hash(source),
            "archive_object_id": f"probe_object_{digest[:12]}",
            "archive_relpath": archive_relpath,
            "source_bytes": stat_size(source),
            "source_mtime": stat_mtime(source),
            "source_content_hash": content_hash,
            "archive_content_hash": content_hash,
            "record_hash": record_hash_for_path(source, source_kind="arbitrary_jsonl_root", default_tz=timezone.utc),
            "source_ref_count": 1,
            "source_refs": [],
        }
        if name == "source_deleted":
            source.unlink()
        elif name == "stale":
            with source.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"role": "assistant", "text": "synthetic stale mutation"}) + "\n")
        elif name == "missing":
            archive_path.unlink()
        elif name == "corrupt":
            archive_path.write_text("corrupted synthetic archive object\n", encoding="utf-8")
        status = _verify_entry_status(entry, base_dir=archive_output_dir)
        probes.append(
            {
                "probe": name,
                "expected_status": expected,
                "status": status["status"],
                "matched": status["status"] == expected,
                "source_exists": status["source_exists"],
                "archive_object_exists": status["archive_object_exists"],
                "archive_hash_matches": status["archive_hash_matches"],
            }
        )
    return probes


def _archive_gate_result(
    entries: list[SnapshotEntry],
    *,
    verification: dict[str, Any],
    export: dict[str, Any],
    status_probes: list[dict[str, Any]],
) -> dict[str, Any]:
    required_statuses = {
        "source_deleted_but_archived",
        "stale_source_changed",
        "missing_archive_object",
        "corrupt_archive_object",
    }
    covered = {str(probe.get("status")) for probe in status_probes if probe.get("matched")}
    hashes_ok = verification.get("status") == "pass"
    export_ok = export.get("status") == "pass"
    statuses_ok = required_statuses <= covered
    duplicates_ok = any(entry.duplicate_group for entry in entries)
    synthetic_passed = bool(entries) and hashes_ok and export_ok and statuses_ok and duplicates_ok
    return {
        "label": "archive_protection_gate",
        "status": "synthetic_fixture_pass" if synthetic_passed else "synthetic_fixture_incomplete",
        "synthetic_proof_passed": synthetic_passed,
        "promotion_ready": False,
        "claim_allowed": False,
        "evidence_basis": "committed_synthetic_fixture_only",
        "hash_verification": hashes_ok,
        "duplicate_detection": duplicates_ok,
        "restore_export_smoke": export_ok,
        "status_handling": statuses_ok,
        "remaining_blockers": [
            "no_private_or_production_archive_writer",
            "no_backup_completeness_claim",
            "no_sanitized_local_aggregate_counters",
            "no_archive_search_or_index",
            "no_encryption_design",
        ],
    }


def _source_refs_for_file(path: Path, *, source_kind: str, source_label: str, default_tz) -> list[dict[str, Any]]:
    refs = []
    try:
        for record in iter_records_for_file(path, source_kind, default_tz):
            refs.append(
                {
                    "source_label": source_label,
                    "line": record.line,
                    "role": record.role,
                    "timestamp": record.timestamp.isoformat() if record.timestamp else None,
                    "text_hash": f"sha256:{hashlib.sha256(record.text.encode('utf-8')).hexdigest()}",
                }
            )
    except AdapterReadError:
        return []
    return refs


def _source_ref_signature(refs: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [
        (
            ref.get("line"),
            ref.get("role"),
            ref.get("timestamp"),
            ref.get("text_hash"),
        )
        for ref in refs
    ]


def _public_source_plan(plan: SourcePlan) -> dict[str, Any]:
    return {
        "selected_root_count": len(plan.roots),
        "rejected_root_count": len(plan.rejected_roots),
        "roots": [
            {
                "root_label": f"root_{index:03d}",
                "kind": root.kind,
                "known": root.known,
                "recursive": root.recursive,
                "exists": root.path.exists(),
            }
            for index, root in enumerate(plan.roots, 1)
        ],
        "cap_status": cap_hits_from_warnings(plan.warnings),
    }


def _invalid_payload(*, errors: list[str], started: float, args, plan: SourcePlan | None = None) -> dict[str, Any]:
    return {
        "archive_snapshot_proof": {
            "schema_version": SCHEMA_VERSION,
            "status": "invalid_request",
            "scope": "committed_synthetic_fixture_only",
            "local_only": True,
            "background_behavior": False,
            "promotion_ready": False,
            "errors": sorted(set(errors)),
            "source_plan": _public_source_plan(plan) if plan else None,
            "limits": {
                "hard_cap_files": getattr(args, "hard_cap_files", None),
                "hard_cap_bytes": getattr(args, "hard_cap_bytes", None),
            },
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        }
    }


def _is_committed_synthetic_fixture_root(root: Path) -> bool:
    resolved = safe_resolve(root)
    fixture_base = safe_resolve(Path(__file__).parents[2] / "tests" / "fixtures" / "archive_snapshot_restore")
    if not _path_contains(fixture_base, resolved):
        return False
    current = resolved if resolved.is_dir() else resolved.parent
    while _path_contains(fixture_base, current):
        if (current / FIXTURE_MARKER).exists():
            return True
        if current == fixture_base:
            break
        current = current.parent
    return False


def _path_contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _sanitize_warnings(warnings: list[str]) -> list[str]:
    sanitized = []
    for warning in warnings:
        if not warning:
            continue
        if "Arbitrary root enabled" in warning:
            item = "Arbitrary root enabled for committed synthetic fixture; strict hard caps apply."
        elif "max file cap" in warning:
            item = "Stopped enumeration at max file cap."
        elif "max byte cap" in warning:
            item = "Stopped enumeration at max byte cap."
        elif "max elapsed cap" in warning:
            item = "Stopped enumeration at max elapsed cap."
        elif "symlink escape" in warning:
            item = "Skipped a symlink escape outside selected root."
        else:
            item = "Warning omitted raw local path details."
        if item not in sanitized:
            sanitized.append(item)
    return sanitized
