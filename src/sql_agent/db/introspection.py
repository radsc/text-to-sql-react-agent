"""Schema introspection: extracts tables, columns, keys, sample rows.

The output feeds two consumers:
- the schema RAG index (Step 3), via ``TableInfo.to_document()``
- the validator's table allow-list (Step 4), via ``list_tables()``
"""

from dataclasses import dataclass

from sqlalchemy import Engine, func, inspect, select, table

from sql_agent.config import get_settings
from sql_agent.db.connection import get_engine


@dataclass
class ColumnInfo:
    name: str
    type: str

@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo]
    row_count: int | None = None

    def to_document(self) -> str:
        """Text indexed in the vector store: table name, columns + types, row count."""
        cols = ",\n".join(f"  {c.name} {c.type}" for c in self.columns)
        doc = f"TABLE {self.name} (\n{cols}\n)"
        if self.row_count is not None:
            doc += f"\n-- {self.row_count} rows"
        return doc

def list_tables(engine: Engine | None = None, allowed: list[str] | None = None) -> list[str]:
    """Table names in the database, filtered by the allow-list (empty = all)."""
    engine = engine or get_engine()
    allowed = get_settings().allowed_table_list if allowed is None else allowed
    names = sorted(inspect(engine).get_table_names())
    if allowed:
        allowed_lower = {t.lower() for t in allowed}
        names = [n for n in names if n.lower() in allowed_lower]
    return names


def get_table_info(engine: Engine, name: str) -> TableInfo:
    insp = inspect(engine)

    columns = [
        ColumnInfo(
            name=c["name"],
            type=str(c["type"]),
        )
        for c in insp.get_columns(name)
    ]
 
    tbl = table(name)
    with engine.connect() as conn:
        row_count = conn.execute(select(func.count()).select_from(tbl)).scalar_one()

    return TableInfo(name, columns, row_count)


def get_schema(
    engine: Engine | None = None,
    allowed: list[str] | None = None,
    sample_size: int = 3,
) -> list[TableInfo]:
    """Introspect every allowed table."""
    engine = engine or get_engine()
    return [get_table_info(engine, t) for t in list_tables(engine, allowed)]
