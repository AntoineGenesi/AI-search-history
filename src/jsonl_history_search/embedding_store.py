from __future__ import annotations

import heapq
import json
import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .embedding_spike import RankedHit


SHARD_MANIFEST_NAME = "embedding-shards-manifest.private.json"


@dataclass(frozen=True)
class StoredVectorRecord:
    doc_id: str
    vector: list[float]
    root_label: str
    parent_doc_id: str | None = None
    chunk_index: int | None = None
    chunk_count: int | None = None


@dataclass(frozen=True)
class StoreConfig:
    backend: str
    model_id: str
    content_fingerprint: str
    document_count: int
    shard_size: int
    batch_size: int
    chunking: dict[str, Any]


class EmbeddingShardStore:
    """Private binary vector shards plus a JSON manifest with no raw text."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.manifest_path = output_dir / SHARD_MANIFEST_NAME

    def reusable_prefix(self, config: StoreConfig, expected_doc_ids: list[str]) -> tuple[dict[str, Any] | None, list[dict[str, Any]], int]:
        manifest = self._read_manifest()
        if not manifest or not self._config_matches(manifest, config):
            return manifest, [], 0
        reusable: list[dict[str, Any]] = []
        offset = 0
        for shard in manifest.get("shards", []):
            count = int(shard.get("record_count") or 0)
            if count <= 0:
                break
            expected = expected_doc_ids[offset : offset + count]
            if not self._shard_is_reusable(shard, expected):
                break
            reusable.append(shard)
            offset += count
        return manifest, reusable, offset

    def write_manifest(self, config: StoreConfig, shards: list[dict[str, Any]], *, complete: bool) -> None:
        payload = {
            "schema_version": 1,
            "backend": config.backend,
            "model_id": config.model_id,
            "content_fingerprint": config.content_fingerprint,
            "document_count": config.document_count,
            "shard_size": config.shard_size,
            "batch_size": config.batch_size,
            "chunking": config.chunking,
            "complete": bool(complete),
            "record_count": sum(int(shard.get("record_count") or 0) for shard in shards),
            "vector_dim": _first_int(shards, "vector_dim"),
            "shards": shards,
            "raw_text_committed": False,
            "private_vectors_committed": False,
        }
        _atomic_write_text(self.manifest_path, json.dumps(payload, indent=2, sort_keys=True))

    def write_shard(self, shard_index: int, records: list[StoredVectorRecord]) -> dict[str, Any]:
        if not records:
            raise ValueError("cannot write an empty embedding shard")
        vector_dim = len(records[0].vector)
        if vector_dim <= 0:
            raise ValueError("cannot write zero-dimensional vectors")
        for record in records:
            if len(record.vector) != vector_dim:
                raise ValueError("all vectors in a shard must have the same dimension")

        vector_name = f"embedding-shard-{shard_index:06d}.f32"
        metadata_name = f"embedding-shard-{shard_index:06d}.meta.private.json"
        vector_path = self.output_dir / vector_name
        metadata_path = self.output_dir / metadata_name
        vector_bytes = b"".join(struct.pack(f"<{vector_dim}f", *record.vector) for record in records)
        _atomic_write_bytes(vector_path, vector_bytes)
        metadata = {
            "schema_version": 1,
            "shard_index": shard_index,
            "record_count": len(records),
            "vector_dim": vector_dim,
            "records": [
                {
                    "doc_id": record.doc_id,
                    "root_label": record.root_label,
                    "parent_doc_id": record.parent_doc_id,
                    "chunk_index": record.chunk_index,
                    "chunk_count": record.chunk_count,
                }
                for record in records
            ],
            "raw_text_committed": False,
        }
        _atomic_write_text(metadata_path, json.dumps(metadata, indent=2, sort_keys=True))
        return {
            "shard_index": shard_index,
            "vector_file": vector_name,
            "metadata_file": metadata_name,
            "record_count": len(records),
            "vector_dim": vector_dim,
            "vector_bytes": len(vector_bytes),
            "metadata_bytes": metadata_path.stat().st_size,
            "doc_ids": [record.doc_id for record in records],
            "root_labels": sorted({record.root_label for record in records}),
        }

    def load_manifest(self) -> dict[str, Any] | None:
        return self._read_manifest()

    def rank_dense(self, query_vector: list[float], *, top_k: int, profiler: Any = None) -> list[RankedHit]:
        manifest = self._read_manifest()
        if not manifest or not query_vector:
            return []
        heap: list[tuple[float, str]] = []
        started = time.perf_counter()
        for shard in manifest.get("shards", []):
            shard_started = time.perf_counter()
            records = self._read_shard(shard)
            if profiler:
                profiler.mark(
                    "shard_query_scan",
                    "shard_loaded",
                    shard_index=int(shard.get("shard_index") or 0),
                    record_count=len(records),
                    vector_dim=int(shard.get("vector_dim") or 0),
                )
            for doc_id, vector in records:
                score = round(_dense_cosine(query_vector, vector), 6)
                item = (score, doc_id)
                if len(heap) < top_k:
                    heapq.heappush(heap, item)
                elif item > heap[0]:
                    heapq.heapreplace(heap, item)
            if profiler:
                profiler.mark(
                    "shard_query_scan",
                    "shard_ranked",
                    shard_index=int(shard.get("shard_index") or 0),
                    elapsed_ms=int(round((time.perf_counter() - shard_started) * 1000)),
                    retained_hits=len(heap),
                )
        hits = [RankedHit(doc_id=doc_id, score=score, rank=0) for score, doc_id in heap]
        hits.sort(key=lambda item: (item.score, item.doc_id), reverse=True)
        if profiler:
            profiler.mark("shard_query_scan", "end", elapsed_ms=int(round((time.perf_counter() - started) * 1000)))
        return [RankedHit(doc_id=hit.doc_id, score=hit.score, rank=index + 1) for index, hit in enumerate(hits)]

    def storage_metrics(self) -> dict[str, Any]:
        manifest = self._read_manifest() or {}
        shards = manifest.get("shards") or []
        vector_bytes = sum(_safe_size(self.output_dir / str(shard.get("vector_file"))) for shard in shards)
        metadata_bytes = sum(_safe_size(self.output_dir / str(shard.get("metadata_file"))) for shard in shards)
        manifest_bytes = _safe_size(self.manifest_path)
        return {
            "manifest_found": bool(manifest),
            "manifest_complete": bool(manifest.get("complete")),
            "shard_count": len(shards),
            "record_count": sum(int(shard.get("record_count") or 0) for shard in shards),
            "vector_dim": int(manifest.get("vector_dim") or 0),
            "vector_bytes": vector_bytes,
            "metadata_bytes": metadata_bytes,
            "manifest_bytes": manifest_bytes,
            "total_bytes": vector_bytes + metadata_bytes + manifest_bytes,
        }

    def _read_manifest(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _config_matches(self, manifest: dict[str, Any], config: StoreConfig) -> bool:
        return (
            manifest.get("schema_version") == 1
            and manifest.get("backend") == config.backend
            and manifest.get("model_id") == config.model_id
            and manifest.get("content_fingerprint") == config.content_fingerprint
            and int(manifest.get("document_count") or -1) == config.document_count
            and int(manifest.get("shard_size") or -1) == config.shard_size
            and int(manifest.get("batch_size") or -1) == config.batch_size
            and (manifest.get("chunking") or {}) == config.chunking
        )

    def _shard_is_reusable(self, shard: dict[str, Any], expected_doc_ids: list[str]) -> bool:
        if list(shard.get("doc_ids") or []) != expected_doc_ids:
            return False
        vector_path = self.output_dir / str(shard.get("vector_file"))
        metadata_path = self.output_dir / str(shard.get("metadata_file"))
        if not vector_path.exists() or not metadata_path.exists():
            return False
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        records = metadata.get("records") or []
        if [str(record.get("doc_id")) for record in records] != expected_doc_ids:
            return False
        count = int(shard.get("record_count") or 0)
        vector_dim = int(shard.get("vector_dim") or 0)
        return vector_path.stat().st_size == count * vector_dim * 4

    def _read_shard(self, shard: dict[str, Any]) -> list[tuple[str, list[float]]]:
        metadata_path = self.output_dir / str(shard.get("metadata_file"))
        vector_path = self.output_dir / str(shard.get("vector_file"))
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        records = metadata.get("records") or []
        vector_dim = int(shard.get("vector_dim") or 0)
        raw = vector_path.read_bytes()
        expected = len(records) * vector_dim * 4
        if len(raw) != expected:
            raise ValueError("embedding shard byte length does not match metadata")
        values = struct.unpack(f"<{len(records) * vector_dim}f", raw) if raw else ()
        result: list[tuple[str, list[float]]] = []
        for index, record in enumerate(records):
            start = index * vector_dim
            result.append((str(record.get("doc_id")), [float(value) for value in values[start : start + vector_dim]]))
        return result


def _first_int(items: Iterable[dict[str, Any]], key: str) -> int:
    for item in items:
        value = int(item.get(key) or 0)
        if value:
            return value
    return 0


def _dense_cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
