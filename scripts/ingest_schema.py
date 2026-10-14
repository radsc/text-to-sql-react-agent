"""Index the database schema (tables + columns) into the Chroma vector store.

Usage:
    python scripts/ingest_schema.py                      # uses DATABASE_URL from .env
    python scripts/ingest_schema.py --query "revenue by customer"   # then test retrieval
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy.engine import make_url  # noqa: E402

from sql_agent.config import get_settings  # noqa: E402
from sql_agent.db.connection import get_engine  # noqa: E402
from sql_agent.db.introspection import get_schema  # noqa: E402
from sql_agent.retrieval.vector_store import index_tables, search_schema  # noqa: E402
from sql_agent.utils.logging import get_logger, setup_logging  # noqa: E402

logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # parser.add_argument(
    #     "--sample-rows", type=int, default=3, help="sample rows per table (0 = none)"
    # )
    parser.add_argument("--query", help="run a test retrieval after indexing")
    args = parser.parse_args()

    setup_logging()
    settings = get_settings()
    db = make_url(settings.database_url).render_as_string(hide_password=True)
    logger.info("Introspecting %s", db)

    tables = get_schema(get_engine())
    for t in tables:
        logger.info("  %-30s %3d columns  %s rows", t.name, len(t.columns), t.row_count)

    count = index_tables(tables)
    logger.info("Indexed %d tables into %s", count, settings.chroma_persist_dir)

    if args.query:
        for hit in search_schema(args.query):
            logger.info("  %.3f  %s", hit["distance"], hit["table"])


if __name__ == "__main__":
    main()
