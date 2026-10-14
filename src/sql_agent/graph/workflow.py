"""LangGraph wiring: a fixed intent gate in front of an autonomous tool-using agent.

    START -> router --(sql)--> agent <-> tools
                   \\                \\
                    -> END           -> finalize -> END
"""

from functools import lru_cache

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from sql_agent.agents.router import route_question
from sql_agent.agents.sql_agent import call_agent, finalize, route_after_agent
from sql_agent.graph.state import AgentState
from sql_agent.tools.sql_tools import TOOLS


def _route_after_router(state: AgentState) -> str:
    # clarify / out_of_scope already carry their reply in `answer`.
    return "agent" if state.get("intent") == "sql" else END


@lru_cache
def build_graph():
    g = StateGraph(AgentState)
    g.add_node("router", route_question)
    g.add_node("agent", call_agent)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_node("finalize", finalize)

    g.add_edge(START, "router")
    g.add_conditional_edges("router", _route_after_router, ["agent", END])
    g.add_conditional_edges("agent", route_after_agent, ["tools", "finalize"])
    g.add_edge("tools", "agent")
    g.add_edge("finalize", END)
    return g.compile()


def run(question: str) -> AgentState:
    return build_graph().invoke(
        {"question": question, "messages": [HumanMessage(question)], "steps": 0}
    )
