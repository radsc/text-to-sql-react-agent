"""Web UI for the deployed agent: ask a question, see the answer, SQL and result rows.

Calls the agent on Vertex AI Agent Engine through the Vertex AI SDK, as
``deploy/deploy.py ask`` does, using your gcloud application-default credentials.
The agent's resource name comes from AGENT_ENGINE_RESOURCE in .env or the sidebar.

Usage:
    streamlit run ui/app.py
"""

import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# projects/PROJECT/locations/LOCATION/reasoningEngines/ID; project and location come from it.
_RESOURCE_RE = re.compile(r"^projects/([^/]+)/locations/([^/]+)/reasoningEngines/(\d+)$")

EXAMPLES = [
    "Top 5 customers by total order value",
    "How many customers do we have?",
    "How many orders are in each status?",
    "Which region has the most customers?",
]

ERROR_HINTS = """Common causes:
- **Cloud SQL is stopped**:
  `gcloud sql instances patch customer-insights --activation-policy=ALWAYS`
- **Your gcloud login expired**: `gcloud auth application-default login`
- **The agent failed**: Logs Explorer, resource type *Reasoning Engine*"""


@st.cache_resource(show_spinner=False)
def get_agent(resource_name: str):
    """The remote agent handle, created once per resource name and reused across reruns."""
    import vertexai
    from vertexai import agent_engines

    project, location, _ = _RESOURCE_RE.match(resource_name).groups()
    vertexai.init(project=project, location=location)
    return agent_engines.get(resource_name)


def render(item: dict) -> None:
    """One assistant reply: answer, SQL and result rows."""
    if item["error"]:
        st.error(item["error"])
        st.markdown(ERROR_HINTS)
        return
    result = item["result"]
    st.markdown(result.get("answer") or "_The agent returned no answer._")
    if result.get("sql"):
        st.code(result["sql"], language="sql")
    rows = result.get("rows") or []
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True)


st.set_page_config(page_title="Text-to-SQL Agent", page_icon="🗄️", layout="wide")

with st.sidebar:
    st.header("Deployed agent")
    resource = st.text_input(
        "Resource name",
        value=os.environ.get("AGENT_ENGINE_RESOURCE", ""),
        help="projects/.../locations/.../reasoningEngines/ID (AGENT_ENGINE_RESOURCE in .env)",
    ).strip()
    st.caption(
        "The first question after the agent has been idle starts a replica, "
        "so it can take up to a minute."
    )
    st.subheader("Try asking")
    for example in EXAMPLES:
        st.button(
            example, use_container_width=True,
            on_click=lambda q=example: st.session_state.update(pending=q),
        )
    if st.button("Clear chat", type="secondary"):
        st.session_state.history = []

st.title("Text-to-SQL Agent")
st.caption("Ask a question about customers, orders and products in the customer_insights database.")

if not _RESOURCE_RE.match(resource):
    st.info("Enter the agent's resource name in the sidebar to start.")
    st.stop()

history = st.session_state.setdefault("history", [])
for item in history:
    with st.chat_message("user"):
        st.markdown(item["question"])
    with st.chat_message("assistant"):
        render(item)

question = st.chat_input("Ask a question...") or st.session_state.pop("pending", None)
if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("The agent is working..."):
            try:
                result, error = get_agent(resource).query(question=question), None
            except Exception as e:  # shown in the chat, so the app keeps running
                result, error = None, f"{type(e).__name__}: {e}"
        item = {"question": question, "result": result, "error": error}
        render(item)
    history.append(item)
