from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _none_if_empty(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value if value else None


@dataclass
class AppConfig:
    qdrant_url: str
    qdrant_api_key: Optional[str]
    qdrant_collection: str
    es_url: str
    es_api_key: Optional[str]
    es_index: str
    markdown_path: str
    rule_code: str
    title: str
    tenant_id: str
    region: str
    product_line: str
    biz_domain: str
    status: str
    effective_from: str
    effective_to: Optional[str]
    ollama_base_url: str
    embedding_model: str
    embedding_model_version: str
    keep_online_versions: int
    chunk_max_chars: int
    chunk_overlap_chars: int


def load_config_from_env() -> AppConfig:
    return AppConfig(
        qdrant_url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
        qdrant_api_key=os.getenv("QDRANT_API_KEY", None),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "rule_clauses"),
        es_url=os.getenv("ES_URL", "http://127.0.0.1:9200"),
        es_api_key=os.getenv("ES_API_KEY", None),
        es_index=os.getenv("ES_INDEX", "rule_clauses"),
        markdown_path=os.getenv("MARKDOWN_PATH", r"D:\AICODE\data\seed\test.md"),
        rule_code=os.getenv("RULE_CODE", "RULE-TEST-001"),
        title=os.getenv("RULE_TITLE", "测试规则文档"),
        tenant_id=os.getenv("TENANT_ID", "default_tenant"),
        region=os.getenv("REGION", "cn"),
        product_line=os.getenv("PRODUCT_LINE", "default_product"),
        biz_domain=os.getenv("BIZ_DOMAIN", "rules"),
        status=os.getenv("RULE_STATUS", "active"),
        effective_from=os.getenv("EFFECTIVE_FROM", "2026-01-01T00:00:00+00:00"),
        effective_to=_none_if_empty(os.getenv("EFFECTIVE_TO", None)),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "nomic-embed-text-v2-moe"),
        embedding_model_version=os.getenv("EMBEDDING_MODEL_VERSION", "local"),
        keep_online_versions=int(os.getenv("KEEP_ONLINE_VERSIONS", "2")),
        chunk_max_chars=int(os.getenv("CHUNK_MAX_CHARS", "1800")),
        chunk_overlap_chars=int(os.getenv("CHUNK_OVERLAP_CHARS", "250")),
    )
