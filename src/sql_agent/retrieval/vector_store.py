"""ChromaDB vector store for schema docs (and, later, few-shot examples).

One document per table: its DDL (columns, types, keys, comments), row count and a
few sample rows. Retrieval returns the tables most relevant to a question.
"""

from functools import lru_cache

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.utils import embedding_functions

from sql_agent.config import get_settings
from sql_agent.db.introspection import TableInfo, get_schema
from sql_agent.utils.logging import get_logger

logger = get_logger(__name__)

SCHEMA_COLLECTION = "schema_tables"

# CHROMA_PERSIST_DIR value for an in-memory index, private to the process. Agent Engine
# runs several workers per replica; sharing one on-disk index, they race creating it.
IN_MEMORY = ":memory:"

# Chroma ships an ONNX build of this model, so it needs no torch install.
_CHROMA_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

@lru_cache
def _embedding_function():
    model = get_settings().embedding_model
    if model == _CHROMA_DEFAULT_MODEL:
        return embedding_functions.DefaultEmbeddingFunction()
    return embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model)

@lru_cache
def get_client() -> chromadb.ClientAPI:
    path = get_settings().chroma_persist_dir
    if path == IN_MEMORY:
        return chromadb.EphemeralClient()
    return chromadb.PersistentClient(path=path)

def get_schema_collection() -> Collection:
    return get_client().get_or_create_collection(
        SCHEMA_COLLECTION,
        embedding_function=_embedding_function(),
        metadata={"hnsw:space": "cosine", "embedding_model": get_settings().embedding_model},
    )


def index_tables(tables: list[TableInfo]) -> int:
    """Sync the schema collection with ``tables``: upsert every table, then drop the rest.

    The collection is updated in place, so if embedding fails the previous index is still
    there. It is only recreated when EMBEDDING_MODEL changed, since old vectors would not
    be comparable with new ones.
    """
    collection = get_schema_collection()
    if (collection.metadata or {}).get("embedding_model") != get_settings().embedding_model:
        get_client().delete_collection(SCHEMA_COLLECTION)
        collection = get_schema_collection()

    ids = [f"table:{t.name}" for t in tables]
    if tables:
        collection.upsert(
            ids=ids,
            documents=[t.to_document() for t in tables],
            metadatas=[_table_metadata(t) for t in tables],
        )
    stale = set(collection.get(include=[])["ids"]) - set(ids)
    if stale:
        collection.delete(ids=sorted(stale))
    return len(tables)


def refresh_schema_index() -> None:
    """Startup hook: re-index the live schema if REINDEX_SCHEMA_ON_STARTUP is set.

    Introspection runs before the index is touched and the index is updated in place, so
    if the database is unreachable or embedding fails the existing index is kept and the
    app still starts.
    """
    settings = get_settings()
    if not settings.reindex_schema_on_startup:
        return
    try:
        count = index_tables(get_schema())
    except Exception:
        logger.exception("Schema reindex failed; keeping the existing index")
        return
    logger.info("Indexed %d tables into %s", count, settings.chroma_persist_dir)


def _table_metadata(t: TableInfo) -> dict:
    meta = {
        "kind": "table",
        "table": t.name,
        "columns": ", ".join(c.name for c in t.columns),
    }
    if t.row_count is not None:
        meta["row_count"] = t.row_count
    return meta


def search_schema(question: str, k: int | None = None) -> list[dict]:
    """Top-k tables for a question: [{table, document, distance}, ...]."""
    collection = get_schema_collection()
    n = min(k or get_settings().schema_top_k, collection.count())
    if n == 0:
        return []
    res = collection.query(query_texts=[question], n_results=n)
    return [
        {"table": meta["table"], "document": doc, "distance": dist}
        for meta, doc, dist in zip(
            res["metadatas"][0], res["documents"][0], res["distances"][0], strict=True
        )
    ]
