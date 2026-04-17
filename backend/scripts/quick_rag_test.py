"""快速 RAG 自检：settings、Chroma 集合、RAGTool.handle。"""
import os
import sys

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.core.settings import load_settings


def main() -> None:
    s = load_settings()
    print("=== settings ===")
    print("chroma_path:", s.chroma_path)
    print("rag_collection_name:", getattr(s, "rag_collection_name", "?"))
    print("rag_embedding_base_url:", getattr(s, "rag_embedding_base_url", "?"))
    print("has embedding key:", bool(s.rag_embedding_api_key))

    print()
    print("=== chroma list_collections ===")
    try:
        import chromadb

        c = chromadb.PersistentClient(path=s.chroma_path)
        cols = c.list_collections()
        print("count:", len(cols))
        for x in cols:
            print(" -", getattr(x, "name", x))
    except Exception as e:
        print("error:", type(e).__name__, e)

    print()
    print('=== RAGTool.handle("充值失败怎么办") ===')
    from app.agents.rag_agent import RAGTool

    r = RAGTool().handle("充值失败怎么办")
    print("status:", r.status)
    print("message:", (r.message or "")[:600])
    if r.error:
        print("error field:", (r.error or "")[:400])


if __name__ == "__main__":
    main()
