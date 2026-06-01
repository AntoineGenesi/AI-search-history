from __future__ import annotations

import json
import math
import re
import time
import tracemalloc
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .query import infer_query
from .ranking import score_record, should_consider
from .records import Record


DEFAULT_EMBEDDING_SPIKE_FIXTURE = Path("benchmarks/retrieval_gold/sanitized_embedding_corpus.json")
VECTORIZER_ID = "embedding_spike_vectorizer"
VECTOR_KIND = "local_fixture_vector"
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:/-]*")

STOPWORDS = {
    "a",
    "about",
    "after",
    "and",
    "as",
    "at",
    "be",
    "by",
    "did",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "the",
    "to",
    "was",
    "we",
    "what",
    "when",
    "where",
    "with",
    "you",
}

CONCEPT_TERMS: dict[str, tuple[str, ...]] = {
    "agent_waste": (
        "agent",
        "agents",
        "aimless",
        "bots",
        "browsing",
        "chat",
        "chats",
        "conversations",
        "digging",
        "history",
        "old",
        "squander",
        "squandered",
        "retrieval",
        "rummaging",
        "search",
        "slow",
        "stale",
        "tool",
        "tools",
        "transcripts",
        "wandering",
        "waste",
        "wasting",
    ),
    "bounded_search": (
        "audit",
        "bounded",
        "budget",
        "caps",
        "candidate",
        "caveats",
        "citations",
        "evidence",
        "first-pass",
        "narrow",
        "proof",
        "ranking",
        "recall",
        "retrieval",
        "shortlist",
        "token",
    ),
    "cross_session_theme": (
        "again",
        "across",
        "different",
        "habit",
        "pattern",
        "resurfacing",
        "repeat",
        "repeated",
        "runs",
        "sessions",
        "theme",
        "workflow",
    ),
    "visual_design": (
        "cards",
        "dense",
        "interface",
        "layout",
        "polished",
        "product",
        "quiet",
        "scanning",
        "visual",
    ),
    "vague_memory": (
        "bad",
        "failure",
        "fuzzy",
        "history",
        "lookup",
        "memory",
        "recollection",
        "retrieval",
        "vague",
    ),
}


@dataclass(frozen=True)
class EmbeddingDocument:
    doc_id: str
    text: str
    timestamp: datetime | None
    role: str
    cwd: str | None


@dataclass(frozen=True)
class RankedHit:
    doc_id: str
    score: float
    rank: int


def load_embedding_fixture(path: Path = DEFAULT_EMBEDDING_SPIKE_FIXTURE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_embedding_fixture(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if payload.get("vectorizer_id") != VECTORIZER_ID:
        errors.append(f"vectorizer_id must be {VECTORIZER_ID}")
    if payload.get("vector_kind") != VECTOR_KIND:
        errors.append(f"vector_kind must be {VECTOR_KIND}")
    privacy = payload.get("privacy")
    if not isinstance(privacy, dict) or privacy.get("raw_private_text_committed") is not False:
        errors.append("privacy.raw_private_text_committed must be false")
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        errors.append("documents must be a non-empty list")
        documents = []
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty list")
        cases = []
    doc_ids = {doc.get("doc_id") for doc in documents if isinstance(doc, dict)}
    for doc in documents:
        if not isinstance(doc, dict):
            errors.append("document entries must be objects")
            continue
        if not isinstance(doc.get("doc_id"), str) or not doc["doc_id"]:
            errors.append("document doc_id is required")
        if not isinstance(doc.get("text"), str) or not doc["text"]:
            errors.append(f"{doc.get('doc_id', 'document')}: text is required")
    seen_families: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            errors.append("case entries must be objects")
            continue
        label = case.get("case_id") or "case"
        if not isinstance(case.get("query"), str) or not case["query"]:
            errors.append(f"{label}: query is required")
        family = case.get("case_family")
        if family not in {"valuable", "worse"}:
            errors.append(f"{label}: case_family must be valuable or worse")
        else:
            seen_families.add(family)
        expected = case.get("expected_vector_behavior")
        if expected not in {"better", "worse"}:
            errors.append(f"{label}: expected_vector_behavior must be better or worse")
        gold_id = case.get("gold_doc_id")
        if gold_id not in doc_ids:
            errors.append(f"{label}: gold_doc_id does not match a document")
    if seen_families != {"valuable", "worse"}:
        errors.append("cases must include both valuable and worse families")
    return errors


def build_embedding_spike_eval(path: Path = DEFAULT_EMBEDDING_SPIKE_FIXTURE) -> dict[str, Any]:
    payload = load_embedding_fixture(path)
    errors = validate_embedding_fixture(payload)
    if errors:
        return {
            "schema_version": 1,
            "status": "invalid_fixture",
            "fixture": str(path),
            "fixture_errors": errors,
        }

    documents = [_document_from_fixture(item) for item in payload["documents"]]
    started = time.perf_counter()
    tracemalloc.start()
    index = build_fixture_index(documents)
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    indexing_ms = round((time.perf_counter() - started) * 1000, 3)
    serialized = json.dumps(index, sort_keys=True, separators=(",", ":")).encode("utf-8")

    case_results = []
    query_latencies: list[float] = []
    for case in payload["cases"]:
        case_started = time.perf_counter()
        vector_hits = rank_vector(case["query"], index)
        vector_latency_ms = round((time.perf_counter() - case_started) * 1000, 3)
        query_latencies.append(vector_latency_ms)
        baseline_hits = rank_current_package_baseline(case["query"], documents)
        case_results.append(_evaluate_case(case, vector_hits, baseline_hits, vector_latency_ms))

    summary = summarize_embedding_spike(case_results, query_latencies)
    return {
        "schema_version": 1,
        "status": "ok",
        "fixture": str(path),
        "vectorizer_id": VECTORIZER_ID,
        "vector_kind": VECTOR_KIND,
        "production_status": "not_production_embedding_backend",
        "data_source": "committed_sanitized_synthetic_fixture",
        "privacy": payload.get("privacy", {}),
        "measurement_note": (
            "This is an honest deterministic fixture-grade vectorizer/evaluator. It uses no model, "
            "GPU, network, private logs, optional package, FAISS, Chroma, sklearn, sentence-transformers, "
            "or persistent production vector DB."
        ),
        "comparison_baseline": {
            "name": "current_package_exact_fuzzy_local_semantic",
            "basis": "infer_query + should_consider + score_record over the same sanitized synthetic records",
        },
        "index_metrics": {
            "document_count": len(documents),
            "feature_count": len(index["idf"]),
            "indexing_time_ms": indexing_ms,
            "serialized_index_bytes": len(serialized),
            "tracemalloc_current_bytes": current_bytes,
            "tracemalloc_peak_bytes": peak_bytes,
        },
        "query_metrics": {
            "median_query_latency_ms": _median(query_latencies),
            "max_query_latency_ms": max(query_latencies) if query_latencies else 0.0,
        },
        "summary": summary,
        "cases": case_results,
        "production_guidance": {
            "default_live_search": "do_not_enable",
            "storage": "optional_local_only_if_future_benchmarks_justify_it",
            "exact_refs_required": True,
            "reason": (
                "Fixture vectors help vague/paraphrase discovery but are weaker for exact quotes, "
                "task slugs, file paths, dates, commands, and ID-like tokens. Live evidence should "
                "continue to use exact/fuzzy/local-semantic retrieval unless a future local-only "
                "embedding backend proves better recall without weakening source references."
            ),
        },
    }


def build_fixture_index(documents: list[EmbeddingDocument]) -> dict[str, Any]:
    doc_features = {doc.doc_id: vectorize_text(doc.text) for doc in documents}
    document_frequency: Counter[str] = Counter()
    for features in doc_features.values():
        document_frequency.update(features.keys())
    count = max(1, len(documents))
    idf = {feature: math.log((1 + count) / (1 + freq)) + 1 for feature, freq in sorted(document_frequency.items())}
    vectors = {
        doc_id: normalize_vector({feature: weight * idf[feature] for feature, weight in features.items()})
        for doc_id, features in doc_features.items()
    }
    return {
        "vectorizer_id": VECTORIZER_ID,
        "vector_kind": VECTOR_KIND,
        "idf": idf,
        "vectors": vectors,
    }


def vectorize_text(text: str) -> dict[str, float]:
    features: Counter[str] = Counter()
    tokens = [token for token in TOKEN_RE.findall(text.lower()) if token not in STOPWORDS]
    for token in tokens:
        if _is_identifier_like(token):
            features[f"shape:{_token_shape(token)}"] += 0.15
            continue
        features[f"tok:{token}"] += 1.0
        for part in re.split(r"[_.:/-]+", token):
            if len(part) >= 4 and part not in STOPWORDS and not _is_identifier_like(part):
                features[f"part:{part}"] += 0.35
    token_set = set(tokens)
    for concept, terms in CONCEPT_TERMS.items():
        overlap = token_set & set(terms)
        if overlap:
            features[f"concept:{concept}"] += min(3.0, 0.85 * len(overlap))
    return dict(features)


def rank_vector(query: str, index: dict[str, Any]) -> list[RankedHit]:
    idf = index["idf"]
    query_features = vectorize_text(query)
    query_vector = normalize_vector(
        {feature: weight * idf.get(feature, 1.0) for feature, weight in query_features.items() if feature in idf}
    )
    hits = [
        RankedHit(doc_id=doc_id, score=round(cosine_similarity(query_vector, vector), 6), rank=0)
        for doc_id, vector in index["vectors"].items()
    ]
    hits.sort(key=lambda item: (item.score, item.doc_id), reverse=True)
    return [RankedHit(doc_id=hit.doc_id, score=hit.score, rank=index + 1) for index, hit in enumerate(hits)]


def rank_current_package_baseline(query: str, documents: list[EmbeddingDocument]) -> list[RankedHit]:
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    intent = infer_query(query, now=now)
    hits: list[RankedHit] = []
    for doc in documents:
        record = Record(
            path=f"sanitized_embedding_corpus/{doc.doc_id}.jsonl",
            line=1,
            role=doc.role,
            text=doc.text,
            timestamp=doc.timestamp,
            cwd=doc.cwd,
        )
        if not should_consider(record, intent):
            continue
        score, _reasons, _artifacts = score_record(record, intent, doc.cwd)
        hits.append(RankedHit(doc_id=doc.doc_id, score=round(score, 6), rank=0))
    hits.sort(key=lambda item: (item.score, item.doc_id), reverse=True)
    return [RankedHit(doc_id=hit.doc_id, score=hit.score, rank=index + 1) for index, hit in enumerate(hits)]


def summarize_embedding_spike(case_results: list[dict[str, Any]], query_latencies: list[float]) -> dict[str, Any]:
    vector_hits = sum(1 for case in case_results if case["vector"]["first_pass_hit"])
    baseline_hits = sum(1 for case in case_results if case["baseline"]["first_pass_hit"])
    valuable = [case for case in case_results if case["case_family"] == "valuable"]
    worse = [case for case in case_results if case["case_family"] == "worse"]
    return {
        "case_count": len(case_results),
        "vector_first_pass_hit_rate": _rate(vector_hits, len(case_results)),
        "baseline_first_pass_hit_rate": _rate(baseline_hits, len(case_results)),
        "vector_mean_recall_at_1": _mean(case["vector"]["recall_at_1"] for case in case_results),
        "vector_mean_recall_at_3": _mean(case["vector"]["recall_at_3"] for case in case_results),
        "vector_mean_recall_at_10": _mean(case["vector"]["recall_at_10"] for case in case_results),
        "median_query_latency_ms": _median(query_latencies),
        "valuable_family_vector_better": sum(1 for case in valuable if case["case_family_effect"] == "vector_better"),
        "worse_family_vector_worse": sum(1 for case in worse if case["case_family_effect"].startswith("vector_worse")),
        "expected_behavior_matches": sum(1 for case in case_results if case["expected_behavior_matched"]),
    }


def normalize_vector(features: dict[str, float]) -> dict[str, float]:
    length = math.sqrt(sum(weight * weight for weight in features.values()))
    if length == 0:
        return {}
    return {feature: round(weight / length, 8) for feature, weight in sorted(features.items())}


def cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(weight * right.get(feature, 0.0) for feature, weight in left.items())


def _evaluate_case(
    case: dict[str, Any],
    vector_hits: list[RankedHit],
    baseline_hits: list[RankedHit],
    vector_latency_ms: float,
) -> dict[str, Any]:
    gold = case["gold_doc_id"]
    vector_rank = _gold_rank(vector_hits, gold)
    baseline_rank = _gold_rank(baseline_hits, gold)
    effect = _case_family_effect(vector_rank, baseline_rank)
    expected = case["expected_vector_behavior"]
    vector_metrics = _strategy_case_metrics(vector_hits, gold)
    vector_metrics["query_latency_ms"] = vector_latency_ms
    return {
        "case_id": case["case_id"],
        "case_family": case["case_family"],
        "scenario": case["scenario"],
        "query": case["query"],
        "gold_doc_id": gold,
        "expected_vector_behavior": expected,
        "expected_behavior_matched": (expected == "better" and effect == "vector_better")
        or (expected == "worse" and effect.startswith("vector_worse")),
        "case_family_effect": effect,
        "vector": vector_metrics,
        "baseline": _strategy_case_metrics(baseline_hits, gold),
    }


def _strategy_case_metrics(hits: list[RankedHit], gold_doc_id: str) -> dict[str, Any]:
    rank = _gold_rank(hits, gold_doc_id)
    return {
        "first_pass_hit": rank == 1,
        "gold_rank": rank,
        "recall_at_1": 1.0 if rank is not None and rank <= 1 else 0.0,
        "recall_at_3": 1.0 if rank is not None and rank <= 3 else 0.0,
        "recall_at_10": 1.0 if rank is not None and rank <= 10 else 0.0,
        "top_hits": [{"doc_id": hit.doc_id, "score": hit.score, "rank": hit.rank} for hit in hits[:3]],
    }


def _document_from_fixture(item: dict[str, Any]) -> EmbeddingDocument:
    raw_timestamp = item.get("timestamp")
    timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00")) if raw_timestamp else None
    return EmbeddingDocument(
        doc_id=item["doc_id"],
        text=item["text"],
        timestamp=timestamp,
        role=item.get("role", "assistant"),
        cwd=item.get("cwd"),
    )


def _gold_rank(hits: list[RankedHit], gold_doc_id: str) -> int | None:
    for hit in hits:
        if hit.doc_id == gold_doc_id:
            return hit.rank
    return None


def _case_family_effect(vector_rank: int | None, baseline_rank: int | None) -> str:
    if vector_rank is None and baseline_rank is None:
        return "tie_miss"
    if vector_rank is None:
        return "vector_worse"
    if baseline_rank is None:
        return "vector_better"
    if vector_rank < baseline_rank:
        return "vector_better"
    if vector_rank > baseline_rank:
        return "vector_worse"
    if baseline_rank == 1:
        return "vector_worse_no_added_value"
    return "tie"


def _is_identifier_like(token: str) -> bool:
    if "/" in token or "." in token or "_" in token:
        return True
    if re.search(r"\d", token) and re.search(r"[a-z]", token):
        return True
    if re.fullmatch(r"\d{4}[-/]\d{2}[-/]\d{2}", token):
        return True
    if len(token) >= 10 and re.search(r"\d", token):
        return True
    return False


def _token_shape(token: str) -> str:
    value = re.sub(r"[a-z]+", "a", token)
    value = re.sub(r"\d+", "0", value)
    return value[:24]


def _mean(values: Any) -> float:
    items = list(values)
    if not items:
        return 0.0
    return round(sum(items) / len(items), 3)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 3)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 3)


def _rate(count: int, total: int) -> float:
    return round(count / total, 3) if total else 0.0
