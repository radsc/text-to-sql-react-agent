import pytest

from sql_agent.guardrails.sql_validator import validate

TABLES = {"customers", "orders"}


def _validate(sql: str, max_rows: int = 100):
    return validate(sql, TABLES, "mysql", max_rows, database="shop")


def test_adds_limit_when_missing():
    result = _validate("SELECT id, name FROM customers")
    assert result.ok
    assert "LIMIT 100" in result.sql


def test_caps_large_limit_and_keeps_small_one():
    assert "LIMIT 100" in _validate("SELECT * FROM orders LIMIT 5000").sql
    assert "LIMIT 10" in _validate("SELECT * FROM orders LIMIT 10").sql


def test_allows_cte_and_join():
    sql = """
        WITH totals AS (SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id)
        SELECT c.name, t.total FROM customers c JOIN totals t ON t.customer_id = c.id
    """
    assert _validate(sql).ok


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE customers SET name = 'x'",
        "DROP TABLE customers",
        "INSERT INTO orders (id) VALUES (1)",
        "SELECT 1; DROP TABLE customers",
        "SELECT * FROM customers FOR UPDATE",
        "SELECT SLEEP(10)",
    ],
)
def test_rejects_unsafe_sql(sql):
    assert not _validate(sql).ok


def test_rejects_unknown_and_cross_database_tables():
    result = _validate("SELECT * FROM payments")
    assert not result.ok
    assert "payments" in result.errors[0]
    assert not _validate("SELECT * FROM mysql.user").ok
    assert _validate("SELECT * FROM shop.customers").ok


def test_rejects_syntax_error():
    result = _validate("SELEC name FROM customers WHERE")
    assert not result.ok
