"""Ask the ReAct Text-to-SQL agent a question and see the decisions it made.

Usage:
    python scripts/ask.py "Top 5 customers by total order value"
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sql_agent.graph.workflow import run  # noqa: E402
from sql_agent.retrieval.vector_store import refresh_schema_index  # noqa: E402
from sql_agent.utils.logging import setup_logging  # noqa: E402

_PREVIEW_ROWS = 20


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    args = parser.parse_args()

    setup_logging()
    refresh_schema_index()
    state = run(args.question)

    print(f"\nIntent: {state.get('intent')}")
    print(f"Steps:  {state.get('steps', 0)}")
    trace = [c for m in state.get("messages", []) for c in getattr(m, "tool_calls", None) or []]
    if trace:
        print("\nAgent decisions:")
        for i, call in enumerate(trace, 1):
            print(f"  {i}. {call['name']}({call['args']})")
    if state.get("sql"):
        print(f"\nSQL:\n{state['sql']}")
    rows = state.get("result", [])
    if rows:
        print(f"\nRows ({len(rows)}, first {min(len(rows), _PREVIEW_ROWS)} shown):")
        for row in rows[:_PREVIEW_ROWS]:
            print(f"  {row}")
    print(f"\nAnswer:\n{state.get('answer', '')}")


if __name__ == "__main__":
    main()
