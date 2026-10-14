import datetime as dt
import json
import pickle
from decimal import Decimal

from sql_agent.agent_engine import SqlAgentApp, to_jsonable


def test_to_jsonable_converts_mysql_values():
    row = {
        "total": Decimal("1234.50"),
        "count": Decimal("7"),
        "created_at": dt.datetime(2026, 1, 2, 3, 4, 5),
        "day": dt.date(2026, 1, 2),
        "blob": b"\x00\x01",
        "name": "Acme",
        "missing": None,
    }
    out = to_jsonable({"rows": [row]})
    json.dumps(out)  # must not raise
    assert out["rows"][0] == {
        "total": 1234.5,
        "count": 7,
        "created_at": "2026-01-02T03:04:05",
        "day": "2026-01-02",
        "blob": "AAE=",
        "name": "Acme",
        "missing": None,
    }


def test_in_memory_chroma_client(monkeypatch):
    # Deployed workers each keep a private index: CHROMA_PERSIST_DIR=:memory: writes no files.
    from types import SimpleNamespace

    from sql_agent.retrieval import vector_store

    monkeypatch.setattr(
        vector_store, "get_settings",
        lambda: SimpleNamespace(chroma_persist_dir=vector_store.IN_MEMORY),
    )
    vector_store.get_client.cache_clear()
    try:
        client = vector_store.get_client()
        assert not client.get_settings().is_persistent
    finally:
        vector_store.get_client.cache_clear()


def test_app_pickles_before_set_up():
    # Agent Engine pickles the instance locally; it must carry no live clients yet.
    restored = pickle.loads(pickle.dumps(SqlAgentApp()))
    assert isinstance(restored, SqlAgentApp)
