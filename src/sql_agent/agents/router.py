"""Router agent: classifies intent (sql | clarify | out_of_scope)."""

from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from sql_agent.config import get_settings
from sql_agent.graph.state import AgentState
from sql_agent.llm.factory import get_llm
from sql_agent.prompts.templates import ROUTER_SYSTEM, dialect_name
from sql_agent.tools.sql_tools import known_tables
from sql_agent.utils.logging import get_logger

logger = get_logger(__name__)

_FALLBACK_MESSAGES = {
    "clarify": "Could you add more detail to your question (metric, entity or time range)?",
    "out_of_scope": "I can only answer questions about the data in this database.",
}


class RouterDecision(BaseModel):
    intent: Literal["sql", "clarify", "out_of_scope"]
    message: str = Field(
        default="",
        description="Clarifying question or refusal shown to the user; empty when intent is sql.",
    )


def _classify(question: str, tables: list[str]) -> RouterDecision:
    system = ROUTER_SYSTEM.format(
        dialect=dialect_name(get_settings().sql_dialect), tables=", ".join(tables)
    )
    llm = get_llm().with_structured_output(RouterDecision)
    return llm.invoke([SystemMessage(system), HumanMessage(question)])


def route_question(state: AgentState) -> AgentState:
    decision = _classify(state["question"], sorted(known_tables()))
    logger.info("Router intent=%s", decision.intent)
    if decision.intent == "sql":
        return {"intent": "sql"}
    return {
        "intent": decision.intent,
        "answer": decision.message or _FALLBACK_MESSAGES[decision.intent],
    }
