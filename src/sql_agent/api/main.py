"""FastAPI entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from sql_agent.graph.workflow import run
from sql_agent.retrieval.vector_store import refresh_schema_index


@asynccontextmanager
async def lifespan(_: FastAPI):
    refresh_schema_index()
    yield


app = FastAPI(title="Text-to-SQL ReAct Agent", version="0.1.0", lifespan=lifespan)


class QueryRequest(BaseModel):
    question: str


class ToolCall(BaseModel):
    tool: str
    args: dict


class QueryResponse(BaseModel):
    sql: str | None = None
    rows: list[dict] = []
    answer: str
    steps: int = 0
    tool_calls: list[ToolCall] = []  # the agent's decisions, in order


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    state = run(req.question)
    calls = [
        ToolCall(tool=c["name"], args=c["args"])
        for m in state.get("messages", [])
        for c in getattr(m, "tool_calls", None) or []
    ]
    return QueryResponse(
        sql=state.get("sql"),
        rows=state.get("result", []),
        answer=state.get("answer", ""),
        steps=state.get("steps", 0),
        tool_calls=calls,
    )
