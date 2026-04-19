from __future__ import annotations

import hashlib
from typing import Dict, List, Tuple

from qdrant_client import QdrantClient
from qdrant_client.http import models


def point_id(rule_code: str, tenant_id: str, version_no: int, clause_no: str, chunk_hash: str) -> str:
    raw = f"{rule_code}|{tenant_id}|{version_no}|{clause_no}|{chunk_hash}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def create_client(cfg):
    return QdrantClient(url=cfg.qdrant_url, api_key=cfg.qdrant_api_key or None)


def ensure_collection(client: QdrantClient, collection: str, vector_size: int):
    collections = [c.name for c in client.get_collections().collections]
    if collection in collections:
        return
    client.create_collection(
        collection_name=collection,
        vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
    )


def get_latest_version(client: QdrantClient, cfg) -> int:
    records, _ = client.scroll(
        collection_name=cfg.qdrant_collection,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(key="rule_code", match=models.MatchValue(value=cfg.rule_code)),
                models.FieldCondition(key="tenant_id", match=models.MatchValue(value=cfg.tenant_id)),
            ]
        ),
        with_payload=True,
        with_vectors=False,
        limit=1000,
    )
    versions = [int((r.payload or {}).get("version_no", 0)) for r in records]
    return max(versions) if versions else 0


def get_current_map(client: QdrantClient, cfg) -> Dict[Tuple[str, str], List[float]]:
    records, _ = client.scroll(
        collection_name=cfg.qdrant_collection,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(key="rule_code", match=models.MatchValue(value=cfg.rule_code)),
                models.FieldCondition(key="tenant_id", match=models.MatchValue(value=cfg.tenant_id)),
                models.FieldCondition(key="is_current", match=models.MatchValue(value=True)),
            ]
        ),
        with_payload=True,
        with_vectors=True,
        limit=2000,
    )
    out: Dict[Tuple[str, str], List[float]] = {}
    for r in records:
        payload = r.payload or {}
        cno = str(payload.get("clause_no", ""))
        ch = str(payload.get("chunk_hash", ""))
        if cno and ch and isinstance(r.vector, list):
            out[(cno, ch)] = r.vector
    return out


def deactivate_current_version(client: QdrantClient, cfg):
    client.set_payload(
        collection_name=cfg.qdrant_collection,
        payload={"is_current": False},
        points=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(key="rule_code", match=models.MatchValue(value=cfg.rule_code)),
                    models.FieldCondition(key="tenant_id", match=models.MatchValue(value=cfg.tenant_id)),
                    models.FieldCondition(key="is_current", match=models.MatchValue(value=True)),
                ]
            )
        ),
    )


def upsert_points(client: QdrantClient, cfg, version_no: int, rows: List[Tuple[str, str, int, List[float]]]):
    points = []
    for clause_no, text, order_no, vector in rows:
        chunk_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        pid = point_id(cfg.rule_code, cfg.tenant_id, version_no, clause_no, chunk_hash)
        payload = {
            "rule_code": cfg.rule_code,
            "title": cfg.title,
            "tenant_id": cfg.tenant_id,
            "region": cfg.region,
            "product_line": cfg.product_line,
            "biz_domain": cfg.biz_domain,
            "status": cfg.status,
            "effective_from": cfg.effective_from,
            "effective_to": cfg.effective_to,
            "model_name": cfg.embedding_model,
            "model_version": cfg.embedding_model_version,
            "version_no": version_no,
            "clause_no": clause_no,
            "order_no": order_no,
            "chunk_hash": chunk_hash,
            "clause_text": text,
            "is_current": True,
        }
        points.append(models.PointStruct(id=pid, vector=vector, payload=payload))
    if points:
        client.upsert(collection_name=cfg.qdrant_collection, points=points, wait=True)


def cleanup_old_versions(client: QdrantClient, cfg, latest_version: int):
    cutoff = latest_version - cfg.keep_online_versions + 1
    if cutoff <= 1:
        return
    client.delete(
        collection_name=cfg.qdrant_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(key="rule_code", match=models.MatchValue(value=cfg.rule_code)),
                    models.FieldCondition(key="tenant_id", match=models.MatchValue(value=cfg.tenant_id)),
                    models.FieldCondition(key="version_no", range=models.Range(lt=cutoff)),
                ]
            )
        ),
        wait=True,
    )
