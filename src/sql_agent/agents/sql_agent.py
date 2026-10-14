"""ReAct SQL agent: the LLM decides which tool to call next and when it is done.

agent -> (tool calls?) -> tools -> agent -> ... -> (no tool calls) -> finalize

The graph only enforces the step budget; exploration, query writing, error recovery and
when to stop are the model's decisions.
"""

from langchain_core.messages import AIMessage, SystemMessage

from sql_agent.config import get_settings
from sql_agent.graph.state import AgentState
from sql_agent.llm.factory import get_llm, message_text
from sql_agent.prompts.templates import AGENT_SYSTEM, dialect_name
from sql_agent.tools.sql_tools import TOOLS, known_tables
from sql_agent.utils.logging import get_logger

logger = get_logger(__name__)


def _system_prompt() -> str:
    s = get_settings()
    return AGENT_SYSTEM.format(
        dialect=dialect_name(s.sql_dialect),
        tables=", ".join(sorted(known_tables())),
        max_rows=s.max_rows,
        max_steps=s.max_agent_steps,
    )


def call_agent(state: AgentState) -> AgentState:
    llm = get_llm().bind_tools(TOOLS)
    response = llm.invoke([SystemMessage(_system_prompt()), *state["messages"]])
    steps = state.get("steps", 0) + 1
    calls = ", ".join(c["name"] for c in getattr(response, "tool_calls", [])) or "(answer)"
    logger.info("Agent step %d/%d: %s", steps, get_settings().max_agent_steps, calls)
    return {"messages": [response], "steps": steps}


def route_after_agent(state: AgentState) -> str:
    last = state["messages"][-1]
    if not getattr(last, "tool_calls", None):
        return "finalize"
    if state.get("steps", 0) >= get_settings().max_agent_steps:
        logger.warning("Agent hit MAX_AGENT_STEPS with tool calls pending")
        return "finalize"
    return "tools"


def finalize(state: AgentState) -> AgentState:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and not last.tool_calls:
        return {"answer": message_text(last).strip()}
    return {
        "answer": f"Sorry, I couldn't finish answering within {state.get('steps', 0)} steps. "
        "Try a more specific question."
    }
