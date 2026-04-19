from __future__ import annotations

from typing import List

import requests


def embed_text(ollama_base_url: str, model: str, text: str) -> List[float]:
    url = f"{ollama_base_url.rstrip('/')}/api/embeddings"
    resp = requests.post(url, json={"model": model, "prompt": text}, timeout=120)
    resp.raise_for_status()
    payload = resp.json()
    embedding = payload.get("embedding")
    if embedding is None:
        raise RuntimeError(f"Ollama embedding response invalid: {payload}")
    return embedding


def detect_embedding_dim(ollama_base_url: str, model: str) -> int:
    vector = embed_text(ollama_base_url, model, "dimension_probe")
    if not vector:
        raise RuntimeError("Embedding vector is empty")
    return len(vector)
