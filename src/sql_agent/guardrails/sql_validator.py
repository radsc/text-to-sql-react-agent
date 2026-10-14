"""SQL validator: sqlglot parse, read-only check, table allow-list, LIMIT injection.

The agent's query tools call this before touching the database, so every query is checked
no matter what the model decides. The read-only DB session in db/connection.py is the
second line of defence.
"""

from dataclasses import dataclass, field

import sqlglot
from sqlalchemy.engine import make_url
from sqlglot import exp
from sqlglot.errors import ParseError

from sql_agent.config import get_settings
from sql_agent.utils.logging import get_logger

logger = get_logger(__name__)

# Statement / clause nodes that must never appear anywhere in the tree (getattr because
# node names vary slightly across sqlglot versions).
_BLOCKED_NODES = tuple(
    getattr(exp, name)
    for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable",
        "Grant", "Revoke", "Command", "Into", "Lock", "Set", "Use",
    )
    if hasattr(exp, name)
)  # fmt: skip

# Functions that can stall the server or read files even inside a SELECT.
_BLOCKED_FUNCTIONS = {"SLEEP", "BENCHMARK", "LOAD_FILE", "GET_LOCK", "PG_SLEEP", "PG_READ_FILE"}


@dataclass
class ValidationResult:
    sql: str
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(
    sql: str,
    allowed_tables: set[str] | frozenset[str],
    dialect: str,
    max_rows: int,
    database: str | None = None,
) -> ValidationResult:
    """Check a generated query; on success ``sql`` is the normalised, LIMIT-capped query."""
    if not sql.strip():
        return ValidationResult(sql, ["The model returned no SQL."])

    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as e:
        return ValidationResult(sql, [f"SQL syntax error: {e}"])

    if len(statements) != 1:
        return ValidationResult(sql, [f"Expected exactly one statement, got {len(statements)}."])
    stmt = statements[0]

    if not isinstance(stmt, exp.Query):
        return ValidationResult(
            sql, [f"Only SELECT / WITH queries are allowed, got {stmt.key.upper()}."]
        )

    errors: list[str] = []
    for node in stmt.walk():
        if isinstance(node, _BLOCKED_NODES):
            errors.append(f"Forbidden operation: {node.key.upper()}.")
        elif isinstance(node, exp.Anonymous) and node.name.upper() in _BLOCKED_FUNCTIONS:
            errors.append(f"Forbidden function: {node.name.upper()}().")

    errors.extend(_check_tables(stmt, allowed_tables, dialect, database))
    if errors:
        return ValidationResult(sql, list(dict.fromkeys(errors)))  # dedupe, keep order

    stmt = _cap_limit(stmt, max_rows)
    return ValidationResult(stmt.sql(dialect=dialect, pretty=True))


def _check_tables(
    stmt: exp.Expression,
    allowed_tables: set[str] | frozenset[str],
    dialect: str,
    database: str | None,
) -> list[str]:
    allowed = {t.lower() for t in allowed_tables}
    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    errors = []
    for table in stmt.find_all(exp.Table):
        name = table.name.lower()
        if not name or name in cte_names:
            continue
        other_db = table.db and (database is None or table.db.lower() != database.lower())
        if table.catalog or other_db:
            errors.append(f"Cross-database reference is not allowed: {table.sql(dialect)}.")
        elif name not in allowed:
            errors.append(
                f"Unknown or disallowed table '{table.name}'. "
                f"Available tables: {', '.join(sorted(allowed_tables))}."
            )
    return errors


def _cap_limit(stmt: exp.Query, max_rows: int) -> exp.Query:
    """Add LIMIT max_rows, or lower an existing literal LIMIT that exceeds it."""
    limit = stmt.args.get("limit")
    current = None
    if isinstance(limit, exp.Limit) and isinstance(limit.expression, exp.Literal):
        try:
            current = int(limit.expression.this)
        except ValueError:
            pass
    if current is None or current > max_rows:
        stmt = stmt.limit(max_rows)
    return stmt


def validate_with_settings(
    sql: str, allowed_tables: set[str] | frozenset[str], max_rows: int | None = None
) -> ValidationResult:
    """``validate`` with dialect, row cap and database name taken from settings."""
    s = get_settings()
    result = validate(
        sql,
        allowed_tables,
        s.sql_dialect,
        max_rows or s.max_rows,
        make_url(s.database_url).database,
    )
    if not result.ok:
        logger.warning("SQL failed validation: %s", "; ".join(result.errors))
    return result
