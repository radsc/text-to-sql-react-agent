"""End-to-end graph tests with a scripted tool-calling LLM and a fake database."""

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage

from sql_agent.agents import router, sql_agent
from sql_agent.config import get_settings
from sql_agent.graph.workflow import run
from sql_agent.tools import sql_tools


class ScriptedToolModel(GenericFakeChatModel):
    """Replays AIMessages (with tool calls) in order; bind_tools is a no-op."""

    def bind_tools(self, tools, **kwargs):
        return self


def _call(name, args, i):
    return {"name": name, "args": args, "id": f"call_{i}", "type": "tool_call"}


def _patch(monkeypatch, responses, execute):
    tables = frozenset({"customers"})
    llm = ScriptedToolModel(messages=iter(responses))
    monkeypatch.setattr(router, "_classify", lambda q, t: router.RouterDecision(intent="sql"))
    monkeypatch.setattr(router, "known_tables", lambda: tables)
    monkeypatch.setattr(sql_agent, "known_tables", lambda: tables)
    monkeypatch.setattr(sql_tools, "known_tables", lambda: tables)
    monkeypatch.setattr(sql_agent, "get_llm", lambda: llm)
    monkeypatch.setattr(sql_tools, "execute_sql", execute)


def test_agent_recovers_from_rejected_query_then_answers(monkeypatch):
    executed = []

    def fake_execute(sql, max_rows=None):
        executed.append(sql)
        return ["name"], [{"name": "Ann"}, {"name": "Bob"}]

    _patch(
        monkeypatch,
        [
            AIMessage("", tool_calls=[_call("run_query", {"sql": "SELECT * FROM payments"}, 1)]),
            AIMessage(
                "", tool_calls=[_call("run_query", {"sql": "SELECT name FROM customers"}, 2)]
            ),
            AIMessage("There are two customers: Ann and Bob."),
        ],
        fake_execute,
    )

    state = run("Who are our customers?")

    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert "Rejected by validator" in tool_msgs[0].content  # agent saw the error...
    assert "payments" in tool_msgs[0].content
    assert len(executed) == 1 and "FROM customers" in executed[0]  # ...and fixed it itself
    assert state["sql"] == executed[0]
    assert state["result"] == [{"name": "Ann"}, {"name": "Bob"}]
    assert state["answer"] == "There are two customers: Ann and Bob."
    assert state["steps"] == 3


def test_parallel_run_query_calls_keep_last_successful_query(monkeypatch):
    def fake_execute(sql, max_rows=None):
        if "COUNT" in sql:
            return ["n"], [{"n": 2}]
        return ["name"], [{"name": "Ann"}, {"name": "Bob"}]

    _patch(
        monkeypatch,
        [
            AIMessage(
                "",
                tool_calls=[
                    _call("run_query", {"sql": "SELECT COUNT(*) AS n FROM customers"}, 1),
                    _call("run_query", {"sql": "SELECT name FROM customers"}, 2),
                    _call("run_query", {"sql": "SELECT * FROM payments"}, 3),  # rejected
                ],
            ),
            AIMessage("There are two customers: Ann and Bob."),
        ],
        fake_execute,
    )

    state = run("How many customers are there, and who are they?")

    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 3 and "Rejected by validator" in tool_msgs[2].content
    assert "FROM customers" in state["sql"] and "COUNT" not in state["sql"]
    assert state["result"] == [{"name": "Ann"}, {"name": "Bob"}]
    assert state["answer"] == "There are two customers: Ann and Bob."


def test_describe_table_rejects_disallowed_table(monkeypatch):
    _patch(
        monkeypatch,
        [
            AIMessage("", tool_calls=[_call("describe_table", {"table": "mysql_users"}, 1)]),
            AIMessage("I can't access that table."),
        ],
        lambda sql, max_rows=None: ([], []),
    )

    state = run("Show me the users table")

    tool_msg = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    assert "Unknown or disallowed table" in tool_msg.content
    assert "sql" not in state


def test_step_budget_stops_runaway_agent(monkeypatch):
    budget = get_settings().max_agent_steps
    _patch(
        monkeypatch,
        [AIMessage("", tool_calls=[_call("list_tables", {}, i)]) for i in range(budget + 5)],
        lambda sql, max_rows=None: ([], []),
    )

    state = run("Loop forever")

    assert state["steps"] == budget
    assert state["answer"].startswith("Sorry, I couldn't finish")


def test_out_of_scope_skips_agent(monkeypatch):
    monkeypatch.setattr(
        router,
        "_classify",
        lambda q, t: router.RouterDecision(intent="out_of_scope", message="Not about the data."),
    )
    monkeypatch.setattr(router, "known_tables", lambda: frozenset({"customers"}))

    state = run("What's the weather?")

    assert state["intent"] == "out_of_scope"
    assert state["answer"] == "Not about the data."
    assert "steps" not in state or state["steps"] == 0
