"""Test, deploy and call the agent on Vertex AI Agent Engine.

Usage:
    # 1. Run SqlAgentApp in-process first (uses your local .env)
    python deploy/deploy.py local "Top 5 customers by total order value"

    # 2. Create the Agent Engine (or update one with --update RESOURCE_NAME).
    #    The provider is --llm-provider if given, else LLM_PROVIDER in .env; its model
    #    (e.g. OPENAI_MODEL) is always taken from .env. Replicas default to 0-2 of
    #    1 vCPU / 2Gi; keep one warm with --min-instances 1 (billed around the clock).
    python deploy/deploy.py deploy --project my-proj --bucket gs://my-proj-agent-staging \
        --cloud-sql-instance my-proj:us-central1:customer-insights

    # 3. Ask the deployed agent
    python deploy/deploy.py ask --project my-proj RESOURCE_NAME "Top 5 customers by revenue"

Secrets are read from Secret Manager at runtime, never shipped with the package:
    sql-agent-database-url   mysql+pymysql://readonly_user:PASSWORD@/customer_insights
    openai-api-key           (only for provider openai)
    anthropic-api-key        (only for provider anthropic)
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from sql_agent.agent_engine import SqlAgentApp  # noqa: E402
from sql_agent.config import get_settings  # noqa: E402

# Per provider: the settings forwarded to the runtime, and its API-key secret (if any).
PROVIDERS = {
    "openai": (["openai_model"], "openai-api-key"),
    "anthropic": (["anthropic_model"], "anthropic-api-key"),
    "vertex": (["vertex_model", "vertex_location"], None),
}


def _secret(name: str) -> dict:
    return {"secret": name, "version": "latest"}


def _env_vars(args: argparse.Namespace) -> dict:
    """Runtime settings: the LLM from --llm-provider or the local .env, the rest fixed here."""
    settings = get_settings()
    provider = (args.llm_provider or settings.llm_provider).strip().lower()
    if provider not in PROVIDERS:
        raise SystemExit(f"LLM_PROVIDER in .env must be one of: {', '.join(PROVIDERS)}")
    fields, key_secret = PROVIDERS[provider]

    env = {
        "APP_ENV": "production",
        "LLM_PROVIDER": provider,
        "CLOUD_SQL_INSTANCE": args.cloud_sql_instance,
        # Each worker keeps its own in-memory index, rebuilt by set_up(); workers sharing
        # an on-disk index race creating it ("table collections already exists").
        "CHROMA_PERSIST_DIR": ":memory:",
        # Chroma downloads its ONNX embedding model to ~/.cache/chroma on first use.
        "HOME": "/tmp",
        "REINDEX_SCHEMA_ON_STARTUP": "true",
        "DATABASE_URL": _secret("sql-agent-database-url"),
    }
    for field in fields:
        value = getattr(settings, field).strip()
        if not value:
            raise SystemExit(f"{field.upper()} is not set in .env")
        env[field.upper()] = value
    if key_secret:
        env[f"{provider.upper()}_API_KEY"] = _secret(key_secret)
    print(f"LLM: {provider} / {env[fields[0].upper()]}")

    for item in args.env:
        key, _, value = item.partition("=")
        env[key] = value
    return env


def run_local(args: argparse.Namespace) -> None:
    os.chdir(ROOT)  # relative paths in .env (CHROMA_PERSIST_DIR) are from the project root
    app = SqlAgentApp()
    app.set_up()
    print(json.dumps(app.query(question=args.question), indent=2))


def deploy(args: argparse.Namespace) -> None:
    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=args.project, location=args.location, staging_bucket=args.bucket)
    # extra_packages keeps paths relative to the cwd; from src/ the package lands as
    # sql_agent/ on the runtime's import path. Only the package is uploaded, not .env.
    os.chdir(SRC)
    requirements = (ROOT / "deploy" / "requirements.txt").read_text().splitlines()
    config = {
        "requirements": [r.split("#")[0].strip() for r in requirements if r.split("#")[0].strip()],
        "extra_packages": ["sql_agent"],
        "env_vars": _env_vars(args),
        # The SDK defaults (1 replica always on, 4 vCPU / 4Gi) bill around the clock;
        # min_instances=0 scales to zero when idle, at the cost of a cold start.
        "min_instances": args.min_instances,
        "max_instances": args.max_instances,
        "resource_limits": {"cpu": args.cpu, "memory": args.memory},
        "display_name": "text-to-sql-react-agent",
        "description": "Text-to-SQL ReAct agent over the customer_insights MySQL database",
    }
    if args.update:
        remote = agent_engines.update(
            resource_name=args.update, agent_engine=SqlAgentApp(), **config
        )
    else:
        remote = agent_engines.create(agent_engine=SqlAgentApp(), **config)
    print(f"Deployed: {remote.resource_name}")


def ask(args: argparse.Namespace) -> None:
    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=args.project, location=args.location)
    remote = agent_engines.get(args.resource_name)
    print(json.dumps(remote.query(question=args.question), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("local", help="run SqlAgentApp in-process")
    p.add_argument("question")
    p.set_defaults(func=run_local)

    p = sub.add_parser("deploy", help="create or update the Agent Engine")
    p.add_argument("--project", required=True)
    p.add_argument("--location", default="us-central1")
    p.add_argument("--bucket", required=True, help="gs:// staging bucket")
    p.add_argument("--cloud-sql-instance", required=True, help="project:region:instance")
    p.add_argument(
        "--llm-provider", choices=list(PROVIDERS), help="defaults to LLM_PROVIDER in .env"
    )
    p.add_argument("--update", metavar="RESOURCE_NAME", help="update this engine in place")
    p.add_argument(
        "--min-instances", type=int, default=0, choices=range(0, 11), metavar="0-10",
        help="replicas kept running; 0 scales to zero when idle (default: 0)",
    )
    p.add_argument(
        "--max-instances", type=int, default=2, metavar="N", help="replica cap (default: 2)"
    )
    p.add_argument(
        "--cpu", default="1", choices=["1", "2", "4", "6", "8"], help="vCPUs per replica"
    )
    p.add_argument("--memory", default="2Gi", help="memory per replica, 1Gi-32Gi (default: 2Gi)")
    p.add_argument(
        "--env", action="append", default=[], metavar="KEY=VALUE",
        help="extra settings, e.g. --env ALLOWED_TABLES=customers,orders",
    )
    p.set_defaults(func=deploy)

    p = sub.add_parser("ask", help="query a deployed Agent Engine")
    p.add_argument("--project", required=True)
    p.add_argument("--location", default="us-central1")
    p.add_argument("resource_name")
    p.add_argument("question")
    p.set_defaults(func=ask)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
