from typing import Any

import httpx

from app.core.settings import load_settings

SETTINGS = load_settings()


def _collection_names(client: Any) -> list[str]:
    out: list[str] = []
    for c in client.list_collections():
        n = getattr(c, "name", None)
        if n:
            out.append(str(n))
    return out


def _get_chroma_collection(client: Any, preferred: str) -> Any:
    """优先使用 preferred；不存在时若库内仅有 1 个集合则回退。"""
    try:
        return client.get_collection(name=preferred)
    except Exception:
        names = _collection_names(client)
        if not names:
            raise RuntimeError(
                f"Chroma 目录下没有任何集合，请确认 CHROMA_PATH={SETTINGS.chroma_path} 且已完成向量入库。"
            ) from None
        if len(names) == 1:
            return client.get_collection(name=names[0])
        raise RuntimeError(
            f"未找到集合「{preferred}」。当前存在的集合：{names}。"
            "请在 .env 中设置 RAG_COLLECTION_NAME 为其中之一。"
        ) from None


def _chunk_id_from_metadata(cid: Any, idx: int) -> int:
    """与 addrag 写入的 chunk_id 对齐；Chroma 可能返回 int/float/str。"""
    if cid is None or isinstance(cid, bool):
        return idx + 1
    if isinstance(cid, int):
        return cid
    if isinstance(cid, float) and cid.is_integer():
        return int(cid)
    if isinstance(cid, str) and cid.strip().isdigit():
        return int(cid.strip())
    return idx + 1


def _assert_addrag_cosine_space(collection: Any) -> None:
    """addrag 建库使用 cosine；若元数据存在且不一致则尽早报错，避免错模型/错库导致静默劣化。"""
    meta = getattr(collection, "metadata", None)
    if not isinstance(meta, dict):
        return
    space = meta.get("hnsw:space")
    if space is None:
        return
    if str(space).lower() != "cosine":
        name = getattr(collection, "name", "?")
        raise RuntimeError(
            f"Chroma 集合「{name}」的 hnsw:space={space!r}，与 AICODE addrag 使用的 cosine 不一致；"
            "请用同一套 addrag 重建集合或检查 RAG_COLLECTION_NAME / CHROMA_PATH。"
        )


def _openai_embeddings_sync(
    text: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: float = 60.0,
) -> list[float]:
    """与 AICODE 一致：OpenAI 兼容 POST /v1/embeddings，返回单条向量。"""
    url = f"{base_url.rstrip('/')}/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {"model": model, "input": text}
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(url, headers=headers, json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"embeddings HTTP {resp.status_code}: {(resp.text or '')[:300]}"
        )
    data = resp.json()
    rows = data.get("data") or []
    if not rows:
        raise RuntimeError("embeddings response missing data")
    emb = rows[0].get("embedding")
    if not isinstance(emb, list):
        raise RuntimeError("embeddings response missing embedding vector")
    return [float(x) for x in emb]


def query_chroma(query: str, chroma_path: str | None = None, top_k: int | None = None) -> list[dict[str, Any]]:
    """
    Chroma 向量检索：先 HTTP 调 Embeddings API（与 AICODE 入库维度一致），再 query_embeddings。
    检索空间与 AICODE addrag 一致：集合 metadata 为 hnsw:space=cosine，近邻 Top-K。
    使用集合名 RAG_COLLECTION_NAME（默认 aicode_kb），与 AICODE `rag_collection_name` 对齐。
    """
    real_chroma_path = chroma_path or SETTINGS.chroma_path
    real_top_k = top_k or SETTINGS.rag_top_k
    api_key = SETTINGS.rag_embedding_api_key
    if not api_key:
        raise RuntimeError(
            "Embedding 需要配置 RAG_EMBEDDING_API_KEY、OPENAI_API_KEY 或 LLM_API_KEY（与 AICODE 一致）。"
        )

    try:
        import chromadb
    except Exception as exc:
        raise RuntimeError(f"chromadb import failed: {exc}") from exc

    try:
        qvec = _openai_embeddings_sync(
            query,
            api_key=api_key,
            base_url=SETTINGS.rag_embedding_base_url,
            model=SETTINGS.rag_embedding_model,
        )
    except Exception as exc:
        raise RuntimeError(f"embeddings request failed: {exc}") from exc

    try:
        client = chromadb.PersistentClient(path=real_chroma_path)
        collection = _get_chroma_collection(client, SETTINGS.rag_collection_name)
        _assert_addrag_cosine_space(collection)
        result = collection.query(
            query_embeddings=[qvec],
            n_results=real_top_k,
            include=["documents", "distances", "ids", "metadatas"],
        )
        docs = result.get("documents", [[]])[0]
        ids = result.get("ids", [[]])[0]
        metas = result.get("metadatas", [[]])[0] or []
        distances = result.get("distances", [[]])[0] if result.get("distances") else []
        name = getattr(collection, "name", None) or SETTINGS.rag_collection_name
        payload: list[dict[str, Any]] = []
        for idx, doc in enumerate(docs):
            meta: dict[str, Any] = {}
            if idx < len(metas) and isinstance(metas[idx], dict):
                meta = metas[idx]
            src = meta.get("source") or meta.get("doc")
            cid = meta.get("chunk_id")
            payload.append(
                {
                    "source": (str(src) if src is not None else f"chroma:{name}"),
                    "chunk_id": _chunk_id_from_metadata(cid, idx),
                    "snippet": doc,
                    "distance": distances[idx] if idx < len(distances) else None,
                    "doc_id": ids[idx] if idx < len(ids) else None,
                }
            )
        return payload
    except Exception as exc:
        raise RuntimeError(f"chroma query failed: {exc}") from exc
