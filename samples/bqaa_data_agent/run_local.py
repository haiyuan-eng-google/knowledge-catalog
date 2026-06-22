"""Run the BQAA Data Agent locally and emit traces to BigQuery via BQAA.

This drives a short scripted conversation through an ADK ``InMemoryRunner`` built
from the ``App`` defined in ``agent.py``. Because the App registers the
BigQueryAgentAnalyticsPlugin, every LLM call / tool call / agent transfer is
written to ``{GOOGLE_CLOUD_PROJECT}.{BIG_QUERY_DATASET_ID}.{BIG_QUERY_TABLE_ID}``.

Prerequisites:
  - `gcloud auth application-default login`
  - The BQAA dataset must already exist:
        bq --location=US mk --dataset "$GOOGLE_CLOUD_PROJECT:agent_analytics"
  - export GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION (and optionally
    BIG_QUERY_DATASET_ID, BIG_QUERY_TABLE_ID, BQ_LOCATION).

Usage:
  python3 run_local.py                       # runs the built-in demo prompts
  python3 run_local.py "your question here"  # runs a single custom prompt
"""

import asyncio
import os
import sys

from google.adk.runners import InMemoryRunner
from google.genai import types

# Ensure local imports resolve when run as a script.
curr_dir = os.path.dirname(os.path.abspath(__file__))
if curr_dir not in sys.path:
  sys.path.insert(0, curr_dir)

from agent import app
from utils import get_bqaa_dataset_id, get_bqaa_table_id, get_consumer_project

DEMO_PROMPTS = [
    "What datasets and tables are available for me to explore?",
    "Pick one relevant table, show me its schema, then summarize what it"
    " contains.",
]

USER_ID = "local-demo-user"


async def _run(prompts: list[str]) -> None:
  project = get_consumer_project()
  print(
      f"BQAA destination: {project}.{get_bqaa_dataset_id()}."
      f"{get_bqaa_table_id()}\n"
  )

  runner = InMemoryRunner(app=app)
  session = await runner.session_service.create_session(
      app_name=runner.app_name, user_id=USER_ID
  )
  try:
    for prompt in prompts:
      print(f"\n=== USER: {prompt}")
      content = types.Content(role="user", parts=[types.Part(text=prompt)])
      async for event in runner.run_async(
          user_id=USER_ID,
          session_id=session.id,
          new_message=content,
      ):
        if event.content and event.content.parts:
          for part in event.content.parts:
            if getattr(part, "text", None):
              print(part.text, end="", flush=True)
            elif getattr(part, "function_call", None):
              print(f"\n[tool call] {part.function_call.name}", flush=True)
      print()
  finally:
    # Closing the runner flushes and closes the BQAA plugin so buffered events
    # are written to BigQuery before the process exits.
    await runner.close()
    print("\nRunner closed; BQAA events flushed to BigQuery.")


def main() -> None:
  prompts = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else DEMO_PROMPTS
  asyncio.run(_run(prompts))


if __name__ == "__main__":
  main()
