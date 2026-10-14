"""Tools the agent chooses between: explore the schema, peek at data, run queries.

Guardrails live inside the tools, not in the graph: every SQL string goes through the
validator before it reaches the database, and only allow-listed tables can be described.
Errors are returned to the model as text so it can decide how to recover.
"""

from functools import lru_cache
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command
from sqlalchemy.exc import SQLAlchemyError
from sqlglot import exp

from sql_agent.config import get_settings
from sql_agent.db.connection import get_engine
from sql_agent.db.introspection import get_table_info
from sql_agent.db.introspection import list_tables as _list_tables
from sql_agent.guardrails.sql_validator import validate_with_settings
from sql_agent.retrieval.vector_store import search_schema as _search_schema
from sql_agent.utils.logging import get_logger

logger = get_logger(__name__)


@lru_cache
def known_tables() -> frozenset[str]:
    """Queryable tables (DB tables filtered by ALLOWED_TABLES), cached per process."""
    return frozenset(_list_tables())


def _unique_columns(names: list[str]) -> list[str]:
    """Suffix repeated names (id, id_2, ...) so a row dict keeps every column."""
    used: set[str] = set()
    out = []
    for name in names:
        candidate, i = name, 1
        while candidate in used:
            i += 1
            candidate = f"{name}_{i}"
        used.add(candidate)
        out.append(candidate)
    return out


def execute_sql(sql: str, max_rows: int | None = None) -> tuple[list[str], list[dict]]:
    """Run an already-validated query; returns (columns, rows) capped at max_rows.

    ``no_parameters`` sends the SQL to the driver verbatim, so ``%`` in LIKE patterns and
    ``:`` in literals aren't mistaken for bind parameters. Repeated column names (e.g.
    ``SELECT c.id, o.id``) are made unique so no value is dropped from the row dicts.
    """
    max_rows = max_rows or get_settings().max_rows
    with get_engine().connect() as conn:
        result = conn.execution_options(no_parameters=True).exec_driver_sql(sql)
        columns = _unique_columns(list(result.keys()))
        rows = [dict(zip(columns, row, strict=True)) for row in result.fetchmany(max_rows)]
    return columns, rows


def format_rows(columns: list[str], rows: list[dict], limit: int) -> str:
    if not rows:
        return "(no rows)"
    shown = rows[:limit]
    lines = [" | ".join(columns)]
    lines += [" | ".join(str(row.get(c)) for c in columns) for row in shown]
    if len(shown) < len(rows):
        lines.append(f"... {len(rows) - len(shown)} more rows not shown")
    return "\n".join(lines)


def _db_error(e: SQLAlchemyError) -> str:
    # The DBAPI message ("Unknown column 'x' in 'field list'") is what the model needs;
    # SQLAlchemy's wrapper adds noise around it.
    return str(getattr(e, "orig", None) or e)


def _resolve_table(name: str) -> str | None:
    by_lower = {t.lower(): t for t in known_tables()}
    return by_lower.get(name.strip().lower())


@tool
def list_tables() -> str:
    """List every table you are allowed to query."""
    return ", ".join(sorted(known_tables())) or "(no tables available)"


@tool
def search_schema(question: str) -> str:
    """Find the tables most relevant to a question, with their columns, types and row counts.

    Use this first; the definitions it returns are enough to write a query, so only call
    describe_table for tables it didn't return.
    """
    allowed = known_tables()
    hits = [h for h in _search_schema(question) if h["table"] in allowed]
    if not hits:
        return "Schema index is empty; use list_tables and describe_table instead."
    return "\n\n".join(h["document"] for h in hits)


@tool
def describe_table(table: str) -> str:
    """Show a table's columns, their types and its row count."""
    name = _resolve_table(table)
    if name is None:
        return f"Unknown or disallowed table '{table}'. Call list_tables to see valid names."
    try:
        return get_table_info(get_engine(), name).to_document()
    except SQLAlchemyError as e:
        return f"Error describing {name}: {_db_error(e)}"


@tool
def sample_rows(table: str, n: int = 3) -> str:
    """Show a few rows of a table, to see real values (formats, codes, categories)."""
    name = _resolve_table(table)
    if name is None:
        return f"Unknown or disallowed table '{table}'. Call list_tables to see valid names."
    n = max(1, n)
    dialect = get_settings().sql_dialect
    check = validate_with_settings(
        exp.select("*").from_(exp.to_table(name, dialect=dialect)).sql(dialect=dialect),
        known_tables(),
        max_rows=n,
    )
    if not check.ok:
        return "Rejected: " + " ".join(check.errors)
    try:
        columns, rows = execute_sql(check.sql, max_rows=n)
    except SQLAlchemyError as e:
        return f"Database error: {_db_error(e)}"
    return format_rows(columns, rows, n)


@tool
def run_query(sql: str, tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Run one read-only SELECT (or WITH) query and see its result.

    The query is validated first (read-only, allowed tables only, LIMIT added). If it is
    rejected or fails, you get the error back and can fix the query and try again.
    The last successful query is what the user sees alongside your answer.
    """
    check = validate_with_settings(sql, known_tables())
    if not check.ok:
        content = "Rejected by validator:\n" + "\n".join(f"- {e}" for e in check.errors)
        return Command(update={"messages": [ToolMessage(content, tool_call_id=tool_call_id)]})

    try:
        columns, rows = execute_sql(check.sql)
    except SQLAlchemyError as e:
        message = _db_error(e)
        logger.warning("Query failed: %s", message)
        return Command(
            update={
                "messages": [ToolMessage(f"Database error: {message}", tool_call_id=tool_call_id)]
            }
        )

    logger.info("Query returned %d rows", len(rows))
    limit = get_settings().tool_result_rows
    content = f"Executed:\n{check.sql}\n\n{len(rows)} rows:\n{format_rows(columns, rows, limit)}"
    return Command(
        update={
            "sql": check.sql,
            "columns": columns,
            "result": rows,
            "messages": [ToolMessage(content, tool_call_id=tool_call_id)],
        }
    )


TOOLS = [list_tables, search_schema, describe_table, sample_rows, run_query]
