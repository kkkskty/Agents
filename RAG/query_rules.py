from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Optional

import requests
from qdrant_client import QdrantClient
from qdrant_client.http import models


def parse_iso_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_effective_now(effective_from: Optional[str], effective_to: Optional[str]) -> bool:
    now = datetime.now(timezone.utc)
    ef = parse_iso_dt(effective_from)
    et = parse_iso_dt(effective_to)
    if ef and now < ef:
        return False
    if et and now > et:
        return False
    return True


def embed_query(ollama_base_url: str, model: str, query: str) -> list[float]:
    resp = requests.post(
        f"{ollama_base_url.rstrip('/')}/api/embeddings",
        json={"model": model, "prompt": query},
        timeout=120,
    )
    resp.raise_for_status()
    payload = resp.json()
    embedding = payload.get("embedding")
    if not embedding:
        raise RuntimeError(f"Embedding response invalid: {payload}")
    return embedding


def main() -> None:
    parser = argparse.ArgumentParser(description="Qdrant rule search with online-like filters")
    parser.add_argument("--query", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--qdrant-collection", default="rule_clauses")
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--embedding-model", default="nomic-embed-text-v2-moe")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    query_vector = embed_query(args.ollama_base_url, args.embedding_model, args.query)

    client = QdrantClient(url=args.qdrant_url)
    search = client.query_points(
        collection_name=args.qdrant_collection,
        query=query_vector,
        limit=max(args.top_k * 3, 20),
        with_payload=True,
        query_filter=models.Filter(
            must=[
                models.FieldCondition(key="is_current", match=models.MatchValue(value=True)),
                models.FieldCondition(key="status", match=models.MatchValue(value="active")),
                models.FieldCondition(key="tenant_id", match=models.MatchValue(value=args.tenant_id)),
                models.FieldCondition(key="region", match=models.MatchValue(value=args.region)),
            ]
        ),
    )

    points = search.points if hasattr(search, "points") else []
    filtered = []
    for p in points:
        payload = p.payload or {}
        if is_effective_now(payload.get("effective_from"), payload.get("effective_to")):
            filtered.append(p)
        if len(filtered) >= args.top_k:
            break

    print(f"hits={len(filtered)}")
    for i, hit in enumerate(filtered, start=1):
        payload = hit.payload or {}
        text = str(payload.get("clause_text", "")).replace("\n", " ")[:220]
        print(
            f"[{i}] score={hit.score:.6f} "
            f"rule={payload.get('rule_code')} version={payload.get('version_no')} clause={payload.get('clause_no')} "
            f"tenant={payload.get('tenant_id')} region={payload.get('region')} text={text}"
        )


if __name__ == "__main__":
    main()
