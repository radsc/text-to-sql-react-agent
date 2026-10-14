"""Application settings loaded from environment variables / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# The project's .env, found from this file rather than the cwd, so scripts and deploy.py
# read the same file wherever they run. Where it doesn't exist (e.g. Agent Engine),
# settings come from environment variables only.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # App
    app_env: str = "development"
    log_level: str = "INFO"

    # LLM
    llm_provider: str = "openai"
    anthropic_api_key: str = ""
    anthropic_model: str = "" # "claude-sonnet-5-5"
    openai_api_key: str = ""
    openai_model: str = "" #"gpt-4o"
    # Claude on Vertex AI (LLM_PROVIDER=vertex): authenticates with the service account,
    # no API key. Agent Engine sets GOOGLE_CLOUD_PROJECT for the deployed agent.
    google_cloud_project: str = ""
    vertex_location: str = "us-east5"
    vertex_model: str = ""
    llm_temperature: float = 0.0
    llm_max_tokens: int = 2048

    # Retrieval
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chroma_persist_dir: str = "./data/vector_store"
    schema_top_k: int = 5
    # Rebuild the schema index from the live database once when the application starts.
    reindex_schema_on_startup: bool = True

    # Database
    # Real credentials belong in .env (DATABASE_URL), which overrides this default.
    database_url: str = "mysql+pymysql://readonly_user:password@localhost:3306/customer_insights"
    # Cloud SQL connection name ("project:region:instance"). When set, connections go
    # through the Cloud SQL Python Connector and DATABASE_URL only supplies user,
    # password and database; its host and port are ignored.
    cloud_sql_instance: str = ""
    sql_dialect: str = "mysql"
    allowed_tables: str = ""

    # Guardrails
    read_only: bool = True
    max_rows: int = 1000
    query_timeout_seconds: int = 30
    # Agent loop budget: max LLM turns (each may call tools) before the agent must stop.
    max_agent_steps: int = 10
    # Rows of a query result shown back to the model; the API still returns every row.
    tool_result_rows: int = 50

    @property
    def allowed_table_list(self) -> list[str]:
        """Parsed ALLOWED_TABLES; an empty list means every table is allowed."""
        return [t.strip() for t in self.allowed_tables.split(",") if t.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
