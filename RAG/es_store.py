from __future__ import annotations

import hashlib
from typing import Dict, List, Tuple

from elasticsearch import Elasticsearch, helpers


def doc_id(rule_code: str, tenant_id: str, version_no: int, clause_no: str, chunk_hash: str) -> str:
    raw = f"{rule_code}|{tenant_id}|{version_no}|{clause_no}|{chunk_hash}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def create_client(cfg) -> Elasticsearch:
    if cfg.es_api_key:
        return Elasticsearch(cfg.es_url, api_key=cfg.es_api_key, verify_certs=False)
    return Elasticsearch(cfg.es_url, verify_certs=False)


def ensure_index(client: Elasticsearch, index: str) -> None:
    if client.indices.exists(index=index):
        return
    client.indices.create(
        index=index,
        body={
            "settings": {
                "analysis": {
                    "analyzer": {
                        "default": {"type": "standard"}
                    }
                }
            },
            "mappings": {
                "properties": {
                    "rule_code": {"type": "keyword"},
                    "title": {"type": "text"},
                    "tenant_id": {"type": "keyword"},
                    "region": {"type": "keyword"},
                    "product_line": {"type": "keyword"},
                    "biz_domain": {"type": "keyword"},
                    "status": {"type": "keyword"},
                    "effective_from": {"type": "date"},
                    "effective_to": {"type": "date"},
                    "model_name": {"type": "keyword"},
                    "model_version": {"type": "keyword"},
                    "version_no": {"type": "integer"},
                    "clause_no": {"type": "keyword"},
                    "order_no": {"type": "integer"},
                    "chunk_hash": {"type": "keyword"},
                    "clause_text": {"type": "text"},
                    "is_current": {"type": "boolean"},
                }
            },
        },
    )


def get_latest_version(client: Elasticsearch, cfg) -> int:
    body = {
        "size": 1,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"rule_code": cfg.rule_code}},
                    {"term": {"tenant_id": cfg.tenant_id}},
                ]
            }
        },
        "sort": [{"version_no": {"order": "desc"}}],
    }
    res = client.search(index=cfg.es_index, body=body)
    hits = res.get("hits", {}).get("hits", [])
    if not hits:
        return 0
    return int(hits[0]["_source"].get("version_no", 0))


def get_current_hash_map(client: Elasticsearch, cfg) -> Dict[Tuple[str, str], Dict]:
    body = {
        "size": 2000,
        "_source": [
            "clause_no",
            "chunk_hash",
            "clause_text",
            "order_no",
        ],
        "query": {
            "bool": {
                "filter": [
                    {"term": {"rule_code": cfg.rule_code}},
                    {"term": {"tenant_id": cfg.tenant_id}},
                    {"term": {"is_current": True}},
                ]
            }
        },
    }
    res = client.search(index=cfg.es_index, body=body)
    hits = res.get("hits", {}).get("hits", [])
    out: Dict[Tuple[str, str], Dict] = {}
    for hit in hits:
        src = hit.get("_source", {})
        cno = str(src.get("clause_no", ""))
        ch = str(src.get("chunk_hash", ""))
        if cno and ch:
            out[(cno, ch)] = src
    return out


def deactivate_current(client: Elasticsearch, cfg) -> None:
    body = {
        "script": {"source": "ctx._source.is_current = false", "lang": "painless"},
        "query": {
            "bool": {
                "filter": [
                    {"term": {"rule_code": cfg.rule_code}},
                    {"term": {"tenant_id": cfg.tenant_id}},
                    {"term": {"is_current": True}},
                ]
            }
        },
    }
    client.update_by_query(index=cfg.es_index, body=body, conflicts="proceed", refresh=True)


def upsert_documents(client: Elasticsearch, cfg, version_no: int, rows: List[Tuple[str, str, int]]) -> None:
    actions = []
    for clause_no, text, order_no in rows:
        chunk_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        actions.append(
            {
                "_op_type": "index",
                "_index": cfg.es_index,
                "_id": doc_id(cfg.rule_code, cfg.tenant_id, version_no, clause_no, chunk_hash),
                "_source": {
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
                },
            }
        )
    if actions:
        helpers.bulk(client, actions, refresh="wait_for")


def cleanup_old_versions(client: Elasticsearch, cfg, latest_version: int) -> None:
    cutoff = latest_version - cfg.keep_online_versions + 1
    if cutoff <= 1:
        return
    body = {
        "query": {
            "bool": {
                "filter": [
                    {"term": {"rule_code": cfg.rule_code}},
                    {"term": {"tenant_id": cfg.tenant_id}},
                    {"range": {"version_no": {"lt": cutoff}}},
                ]
            }
        }
    }
    client.delete_by_query(index=cfg.es_index, body=body, conflicts="proceed", refresh=True)
