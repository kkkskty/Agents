from __future__ import annotations

import argparse
import hashlib
import logging

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):
        return False

from config import load_config_from_env
from doc_parser import read_markdown, split_clauses
from embedding import detect_embedding_dim, embed_text
from es_store import (
    cleanup_old_versions as cleanup_old_es_versions,
    create_client as create_es_client,
    deactivate_current as deactivate_current_es,
    ensure_index as ensure_es_index,
    get_current_hash_map as get_current_es_map,
    get_latest_version as get_latest_es_version,
    upsert_documents as upsert_es_documents,
)
from qdrant_store import (
    cleanup_old_versions,
    create_client,
    deactivate_current_version,
    ensure_collection,
    get_current_map,
    get_latest_version,
    upsert_points,
)


def parse_args():
    parser = argparse.ArgumentParser(description="RAG rules bootstrap script")
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    parser.add_argument("--dry-run", action="store_true", help="Only parse/split document")
    return parser.parse_args()


def main():
    args = parse_args()
    load_dotenv(args.env_file)
    cfg = load_config_from_env()

    logging.info("Reading markdown from: %s", cfg.markdown_path)
    markdown = read_markdown(cfg.markdown_path)
    chunks = split_clauses(markdown, cfg.chunk_max_chars, cfg.chunk_overlap_chars)
    logging.info("Total chunks: %s", len(chunks))

    if args.dry_run:
        logging.info("Dry run finished")
        return

    embedding_dim = detect_embedding_dim(cfg.ollama_base_url, cfg.embedding_model)
    logging.info("Embedding dimension: %s", embedding_dim)

    client = create_client(cfg)
    ensure_collection(client, cfg.qdrant_collection, embedding_dim)
    es_client = create_es_client(cfg)
    ensure_es_index(es_client, cfg.es_index)

    old_map = get_current_map(client, cfg)
    es_old_map = get_current_es_map(es_client, cfg)
    new_version_no = max(get_latest_version(client, cfg), get_latest_es_version(es_client, cfg)) + 1

    reused, changed = 0, 0
    point_rows = []
    for clause_no, text, order_no in chunks:
        chunk_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        old_vec = old_map.get((clause_no, chunk_hash))
        if old_vec:
            point_rows.append((clause_no, text, order_no, old_vec))
            reused += 1
        else:
            point_rows.append((clause_no, text, order_no, embed_text(cfg.ollama_base_url, cfg.embedding_model, text)))
            changed += 1

    deactivate_current_version(client, cfg)
    upsert_points(client, cfg, new_version_no, point_rows)
    cleanup_old_versions(client, cfg, new_version_no)
    deactivate_current_es(es_client, cfg)
    upsert_es_documents(es_client, cfg, new_version_no, [(c, t, o) for c, t, o, _ in point_rows])
    cleanup_old_es_versions(es_client, cfg, new_version_no)
    es_reused = 0
    for clause_no, text, _order_no, _vector in point_rows:
        chunk_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if (clause_no, chunk_hash) in es_old_map:
            es_reused += 1

    logging.info("Done. version=%s reused=%s changed=%s", new_version_no, reused, changed)
    logging.info("Elasticsearch synced. version=%s reused=%s indexed=%s", new_version_no, es_reused, len(point_rows))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
