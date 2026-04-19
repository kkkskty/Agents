import hashlib
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx
from elasticsearch import Elasticsearch
from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.core.settings import load_settings

SETTINGS = load_settings()


class VectorRetriever(Protocol):
    def retrieve(self, query: str, *, top_k: int) -> list[dict[str, Any]]: ...


def _none_if_empty(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _parse_iso_dt(value: Any) -> datetime | None:
    s = _none_if_empty(value)
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_effective_now(effective_from: Any, effective_to: Any) -> bool:
    now = datetime.now(timezone.utc)
    ef = _parse_iso_dt(effective_from)
    et = _parse_iso_dt(effective_to)
    if ef and now < ef:
        return False
    if et and now > et:
        return False
    return True


def _ollama_embedding(query: str) -> list[float]:
    base = SETTINGS.rag_embedding_base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = f"{base}/api/embeddings"
    last_err: str | None = None
    # 本地 Ollama 偶发 502（常见于系统代理/短暂抖动）；禁用环境代理并做轻量重试。
    for _attempt in range(3):
        try:
            with httpx.Client(timeout=60.0, trust_env=False) as client:
                resp = client.post(url, json={"model": SETTINGS.rag_embedding_model, "prompt": query})
            if resp.status_code >= 400:
                last_err = f"ollama embeddings HTTP {resp.status_code}: {(resp.text or '')[:200]}"
                continue
            payload = resp.json()
            embedding = payload.get("embedding")
            if not isinstance(embedding, list) or not embedding:
                last_err = "ollama embeddings response invalid"
                continue
            return [float(x) for x in embedding]
        except Exception as exc:
            last_err = str(exc)
            continue
    raise RuntimeError(last_err or "ollama embeddings request failed")


def query_qdrant(query: str, top_k: int | None = None) -> list[dict[str, Any]]:
    real_top_k = top_k or SETTINGS.rag_top_k
    qvec = _ollama_embedding(query)
    client = QdrantClient(url=SETTINGS.qdrant_url, api_key=SETTINGS.qdrant_api_key or None)
    result = client.query_points(
        collection_name=SETTINGS.qdrant_collection,
        query=qvec,
        limit=max(real_top_k * 3, 20),
        with_payload=True,
        query_filter=models.Filter(
            must=[
                models.FieldCondition(key="is_current", match=models.MatchValue(value=True)),
                models.FieldCondition(key="status", match=models.MatchValue(value="active")),
                models.FieldCondition(key="tenant_id", match=models.MatchValue(value=SETTINGS.rag_filter_tenant_id)),
                models.FieldCondition(key="region", match=models.MatchValue(value=SETTINGS.rag_filter_region)),
            ]
        ),
    )
    rows = result.points if hasattr(result, "points") else []
    out: list[dict[str, Any]] = []
    for r in rows:
        p = r.payload or {}
        if not _is_effective_now(p.get("effective_from"), p.get("effective_to")):
            continue
        out.append(
            {
                "source": f"qdrant:{SETTINGS.qdrant_collection}",
                "chunk_id": p.get("clause_no") or p.get("order_no"),
                "snippet": p.get("clause_text"),
                "distance": 1.0 - float(r.score),
                "doc_id": hashlib.md5(str(p).encode("utf-8")).hexdigest(),
            }
        )
        if len(out) >= real_top_k:
            break
    return out


def query_bm25(query: str, top_k: int | None = None) -> list[dict[str, Any]]:
    real_top_k = top_k or SETTINGS.rag_top_k
    es = Elasticsearch(SETTINGS.es_url, api_key=SETTINGS.es_api_key or None, verify_certs=False)
    body = {
        "size": max(real_top_k * 3, 20),
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": query,
                            "fields": ["title^2", "clause_text", "rule_code^3", "clause_no^2"],
                            "type": "best_fields",
                        }
                    }
                ],
                "filter": [
                    {"term": {"is_current": True}},
                    {"term": {"status": "active"}},
                    {"term": {"tenant_id": SETTINGS.rag_filter_tenant_id}},
                    {"term": {"region": SETTINGS.rag_filter_region}},
                ],
            }
        },
    }
    res = es.search(index=SETTINGS.es_index, body=body)
    hits = res.get("hits", {}).get("hits", [])
    out: list[dict[str, Any]] = []
    for row in hits:
        src = row.get("_source", {})
        if not _is_effective_now(src.get("effective_from"), src.get("effective_to")):
            continue
        out.append(
            {
                "source": f"es:{SETTINGS.es_index}",
                "chunk_id": src.get("clause_no") or src.get("order_no"),
                "snippet": src.get("clause_text"),
                "distance": -float(row.get("_score", 0.0)),  # 统一给 merge 使用，值越小代表越相关
                "doc_id": row.get("_id"),
            }
        )
        if len(out) >= real_top_k:
            break
    return out


class QdrantRetriever:
    def retrieve(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        return query_qdrant(query, top_k=top_k)


class ElasticRetriever:
    def retrieve(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        return query_bm25(query, top_k=top_k)


class HybridRetriever:
    @staticmethod
    def _hit_key(hit: dict[str, Any]) -> str:
        chunk_id = str(hit.get("chunk_id") or "")
        snippet = re.sub(r"\s+", " ", str(hit.get("snippet") or "")).strip()[:120]
        return f"{chunk_id}|{snippet}"

    def _rrf_fuse(
        self,
        bm25_hits: list[dict[str, Any]],
        vector_hits: list[dict[str, Any]],
        *,
        top_k: int,
        rank_constant: int = 60,
    ) -> list[dict[str, Any]]:
        fused_scores: dict[str, float] = defaultdict(float)
        exemplars: dict[str, dict[str, Any]] = {}

        for rank, hit in enumerate(bm25_hits, start=1):
            key = self._hit_key(hit)
            fused_scores[key] += 1.0 / (rank_constant + rank)
            if key not in exemplars:
                exemplars[key] = dict(hit)

        for rank, hit in enumerate(vector_hits, start=1):
            key = self._hit_key(hit)
            fused_scores[key] += 1.0 / (rank_constant + rank)
            cur = exemplars.get(key)
            if cur is None or _score_to_similarity(hit.get("distance")) > _score_to_similarity(cur.get("distance")):
                exemplars[key] = dict(hit)

        ranked_keys = sorted(fused_scores.keys(), key=lambda k: fused_scores[k], reverse=True)[:top_k]
        out: list[dict[str, Any]] = []
        for key in ranked_keys:
            hit = dict(exemplars[key])
            # 将 RRF 分数映射回“distance 越小越好”口径，保持后续 merge/re-rank 兼容。
            hit["distance"] = (1.0 / max(fused_scores[key], 1e-6)) - 1.0
            hit["source"] = f"hybrid_rrf:{hit.get('source')}"
            out.append(hit)
        return out

    def retrieve(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        bm25_hits = query_bm25(query, top_k=top_k)
        vector_hits = query_qdrant(query, top_k=top_k)
        return self._rrf_fuse(bm25_hits, vector_hits, top_k=top_k)


def get_vector_retriever() -> VectorRetriever:
    backend = SETTINGS.rag_vector_backend
    if backend == "elasticsearch":
        return ElasticRetriever()
    if backend == "qdrant":
        return QdrantRetriever()
    return HybridRetriever()


def rewrite_queries(query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []
    compact = re.sub(r"\s+", " ", q)
    variants = [compact]
    if compact != q:
        variants.append(q)
    # 轻量中文检索增强：替换常见英文拼写误差与术语
    synonym_pairs = {
        "retrival": "retrieval",
        "gereration": "generation",
        "向量库": "向量数据库",
    }
    rewritten = compact
    for src, dst in synonym_pairs.items():
        rewritten = rewritten.replace(src, dst)
    if rewritten != compact:
        variants.append(rewritten)
    dedup: list[str] = []
    seen: set[str] = set()
    for item in variants:
        if item not in seen:
            seen.add(item)
            dedup.append(item)
    return dedup


def _score_to_similarity(distance: Any) -> float:
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return 0.0
    # 统一使用“distance 越小越相关”口径。对于 ES 我们已把 score 存成负数。
    return 1.0 / (1.0 + max(0.0, d))


def merge_retrieval_hits(
    all_hits: list[dict[str, Any]],
    *,
    score_threshold: float,
    enable_rerank: bool,
    query: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for hit in all_hits:
        source = str(hit.get("source") or "")
        chunk_id = str(hit.get("chunk_id") or "")
        doc_id = str(hit.get("doc_id") or "")
        key = f"{doc_id}|{source}|{chunk_id}"
        sim = _score_to_similarity(hit.get("distance"))
        if sim < score_threshold:
            continue
        cur = grouped.get(key)
        if cur is None or sim > _score_to_similarity(cur.get("distance")):
            grouped[key] = hit

    merged = list(grouped.values())
    if not enable_rerank:
        return merged

    query_terms = [x for x in re.split(r"[\s,，。；;]+", query.lower()) if x]
    weights: dict[int, float] = defaultdict(float)
    for idx, hit in enumerate(merged):
        text = str(hit.get("snippet") or "").lower()
        coverage = 0.0
        if query_terms:
            matched = sum(1 for t in query_terms if t and t in text)
            coverage = matched / max(1, len(query_terms))
        weights[idx] = 0.7 * _score_to_similarity(hit.get("distance")) + 0.3 * coverage
    return [merged[i] for i in sorted(range(len(merged)), key=lambda x: weights[x], reverse=True)]


def build_context_from_hits(hits: list[dict[str, Any]], *, budget_chars: int, max_snippets: int) -> str:
    parts: list[str] = []
    used = 0
    count = 0
    for hit in hits:
        snippet = str(hit.get("snippet") or "").strip()
        if not snippet:
            continue
        source = str(hit.get("source") or "unknown")
        cid = hit.get("chunk_id")
        block = f"[{source}#{cid}] {snippet}"
        if used + len(block) > budget_chars:
            break
        parts.append(block)
        used += len(block)
        count += 1
        if count >= max_snippets:
            break
    return "\n".join(parts)


def generate_answer(query: str, context: str) -> str:
    if not context.strip():
        return "当前知识库没有检索到足够证据，请补充更具体的规则名称、流程阶段或关键词。"
    lines = [ln for ln in context.splitlines() if ln.strip()]
    top = lines[0] if lines else context[:180]
    return f"基于检索到的证据，与你问题最相关的内容是：{top}"


def postprocess_answer(answer: str, hits: list[dict[str, Any]]) -> tuple[str, float, list[str]]:
    text = re.sub(r"\s+", " ", (answer or "").strip())
    flags: list[str] = []
    if not hits:
        flags.append("no_citation")
    if len(text) < 20:
        flags.append("too_short")
    confidence = min(0.98, 0.35 + 0.1 * len(hits))
    if "没有检索到足够证据" in text:
        confidence = min(confidence, 0.4)
        flags.append("insufficient_evidence")
    return text, confidence, flags
