"""Vertex AI Agent Engine entrypoint.

Agent Engine pickles a ``SqlAgentApp`` instance on the deploying machine, unpickles it in
its managed runtime, calls ``set_up()`` once per replica and then ``query()`` per request.
So the instance holds no state at pickle time: the engine, LLM client, Chroma index and
compiled graph are all created in ``set_up()``, on the server.

Each replica runs several worker processes, each calling ``set_up()`` at the same time.
Each keeps its own in-memory index (CHROMA_PERSIST_DIR=:memory:), and they build it one
at a time, so they don't all download Chroma's embedding model into the shared cache at once.
"""

import base64
import contextlib
import datetime as dt
import os
import tempfile
import uuid
from decimal import Decimal
from typing import Any

_SETUP_LOCK = os.path.join(tempfile.gettempdir(), "sql_agent_setup.lock")


@contextlib.contextmanager
def _one_process_at_a_time(path: str):
    """Exclusive file lock across the replica's workers; a no-op where fcntl is missing."""
    try:
        import fcntl
    except ImportError:  # Windows, when testing locally: a single process
        yield
        return
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def to_jsonable(value: Any) -> Any:
    """Convert DB values (Decimal, dates, bytes, ...) to JSON types; Agent Engine requires it."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


class SqlAgentApp:
    """The deployed agent: ``query(question=...)`` returns the same fields as POST /query."""

    def set_up(self) -> None:
        from sql_agent.graph.workflow import build_graph
        from sql_agent.retrieval.vector_store import refresh_schema_index
        from sql_agent.utils.logging import setup_logging

        setup_logging()
        with _one_process_at_a_time(_SETUP_LOCK):
            refresh_schema_index()
        self._graph = build_graph()

    def query(self, question: str) -> dict:
        from langchain_core.messages import HumanMessage

        state = self._graph.invoke(
            {"question": question, "messages": [HumanMessage(question)], "steps": 0}
        )
        tool_calls = [
            {"tool": c["name"], "args": c["args"]}
            for m in state.get("messages", [])
            for c in getattr(m, "tool_calls", None) or []
        ]
        return to_jsonable(
            {
                "sql": state.get("sql"),
                "rows": state.get("result", []),
                "answer": state.get("answer", ""),
                "steps": state.get("steps", 0),
                "tool_calls": tool_calls,
            }
        )
