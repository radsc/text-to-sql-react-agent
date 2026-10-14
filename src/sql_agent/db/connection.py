"""SQLAlchemy engine factory for MySQL (read-only, with timeout).

Every new connection runs ``SET SESSION TRANSACTION READ ONLY`` and sets
``max_execution_time``, so writes and runaway queries fail inside MySQL itself,
independently of the SQL validator.

With CLOUD_SQL_INSTANCE set (Agent Engine deployment), connections are opened through
the Cloud SQL Python Connector instead of the host in DATABASE_URL.
"""

from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url

from sql_agent.config import get_settings


def _cloud_sql_creator(database_url: str, instance: str):
    """Connection factory for Cloud SQL via the Python Connector (TLS + IAM, no VPC needed)."""
    from google.cloud.sql.connector import Connector

    url = make_url(database_url)
    connector = Connector()

    def connect():
        return connector.connect(
            instance, "pymysql", user=url.username, password=url.password, db=url.database
        )

    return connect


def create_db_engine(
    database_url: str,
    read_only: bool = True,
    timeout_seconds: int = 30,
    cloud_sql_instance: str = "",
) -> Engine:
    if cloud_sql_instance:
        engine = create_engine(
            "mysql+pymysql://",
            creator=_cloud_sql_creator(database_url, cloud_sql_instance),
            pool_pre_ping=True,
        )
    else:
        engine = create_engine(database_url, pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, connection_record):
        with dbapi_conn.cursor() as cur:
            # max_execution_time only applies to SELECT, which is all we allow.
            cur.execute(f"SET SESSION max_execution_time = {int(timeout_seconds) * 1000}")
            if read_only:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
        dbapi_conn.commit()

    return engine


@lru_cache
def get_engine() -> Engine:
    """Shared engine built from settings (read-only unless READ_ONLY=false)."""
    s = get_settings()
    return create_db_engine(
        s.database_url, s.read_only, s.query_timeout_seconds, s.cloud_sql_instance
    )
