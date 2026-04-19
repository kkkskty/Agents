from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Optional

from elasticsearch import Elasticsearch


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


def main() -> None:
    parser = argparse.ArgumentParser(description="BM25 search with online filters")
    parser.add_argument("--query", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--es-url", default="http://127.0.0.1:9200")
    parser.add_argument("--es-api-key", default="")
    parser.add_argument("--es-index", default="rule_clauses")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    client = Elasticsearch(args.es_url, api_key=args.es_api_key or None, verify_certs=False)
    body = {
        "size": max(args.top_k * 3, 20),
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": args.query,
                            "fields": ["title^2", "clause_text", "rule_code^3", "clause_no^2"],
                            "type": "best_fields",
                        }
                    }
                ],
                "filter": [
                    {"term": {"is_current": True}},
                    {"term": {"status": "active"}},
                    {"term": {"tenant_id": args.tenant_id}},
                    {"term": {"region": args.region}},
                ],
            }
        },
    }
    res = client.search(index=args.es_index, body=body)
    hits = res.get("hits", {}).get("hits", [])

    rows = []
    for hit in hits:
        src = hit.get("_source", {})
        if not is_effective_now(src.get("effective_from"), src.get("effective_to")):
            continue
        rows.append(hit)
        if len(rows) >= args.top_k:
            break

    print(f"hits={len(rows)}")
    for i, hit in enumerate(rows, start=1):
        src = hit.get("_source", {})
        text = str(src.get("clause_text", "")).replace("\n", " ")[:220]
        print(
            f"[{i}] score={hit.get('_score', 0):.6f} "
            f"rule={src.get('rule_code')} version={src.get('version_no')} clause={src.get('clause_no')} "
            f"tenant={src.get('tenant_id')} region={src.get('region')} text={text}"
        )


if __name__ == "__main__":
    main()
