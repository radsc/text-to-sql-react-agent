"""Shared state for the router -> ReAct agent graph."""

from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def _last(_old, new):
    """Keep the newest write; parallel tool calls in one step are applied in call order."""
    return new


class AgentState(TypedDict, total=False):
    question: str
    intent: Literal["sql", "clarify", "out_of_scope"]
    # The agent's scratchpad: question, its tool calls and the tool results.
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int  # LLM turns taken by the agent so far
    # Last query that ran successfully (set by the run_query tool). The reducer lets
    # several run_query calls in one turn write these keys; the last successful one wins.
    # The `| None` keeps them absent until a query succeeds, as before.
    sql: Annotated[str | None, _last]
    columns: Annotated[list[str] | None, _last]
    result: Annotated[list[dict] | None, _last]
    answer: str
