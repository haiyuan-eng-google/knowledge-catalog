"""Consume BQAA traces with the BigQuery-Agent-Analytics-SDK.

This closes the loop started by ``agent.py`` / ``run_local.py``: the agent logs
every step to BigQuery via the BigQuery Agent Analytics plugin, and this script
reads those traces back with the open-source
`BigQuery-Agent-Analytics-SDK <https://github.com/GoogleCloudPlatform/BigQuery-Agent-Analytics-SDK>`_
to reconstruct a trace and score sessions with code-based metrics.

Install the SDK first:
    pip install -r requirements-analysis.txt

Then (with ADC + the same env as the agent):
    python3 analyze.py
    python3 analyze.py --limit 20 --latency-ms 8000 --max-turns 12
"""

import argparse
import os
import sys

from bigquery_agent_analytics import Client, TraceFilter

# The prebuilt code-based evaluator is named ``CodeEvaluator`` in current SDK
# releases; older builds exposed it as ``SystemEvaluator``. Support both.
try:
  from bigquery_agent_analytics import CodeEvaluator as CodeEvaluator
except ImportError:  # pragma: no cover - older SDK fallback
  from bigquery_agent_analytics import SystemEvaluator as CodeEvaluator

# Ensure local imports resolve when run as a script.
curr_dir = os.path.dirname(os.path.abspath(__file__))
if curr_dir not in sys.path:
  sys.path.insert(0, curr_dir)

from utils import (
    get_bqaa_dataset_id,
    get_bqaa_location,
    get_bqaa_table_id,
    get_consumer_project,
)


def _make_client() -> Client:
  return Client(
      project_id=get_consumer_project(),
      dataset_id=get_bqaa_dataset_id(),
      table_id=get_bqaa_table_id(),
      location=get_bqaa_location(),
  )


def reconstruct_latest_trace(client: Client, limit: int) -> None:
  """Lists recent traces and renders the most recent one as a tree."""
  traces = client.list_traces(TraceFilter(limit=limit))
  if not traces:
    print(
        "No traces found yet. Run the agent first (run_local.py / adk web)"
        " so the BQAA plugin writes events, then re-run analyze.py."
    )
    return

  print(f"Found {len(traces)} recent session trace(s):")
  for t in traces:
    print(f"  - session={t.session_id}  trace={t.trace_id}  spans={len(t.spans)}")

  latest = traces[0]
  print(f"\n=== Reconstructed trace for session {latest.session_id} ===")
  print(latest.render(format="tree"))


def evaluate_sessions(
    client: Client, limit: int, latency_ms: float, max_turns: int
) -> None:
  """Runs a few code-based (deterministic) evaluators over recent sessions."""
  filters = TraceFilter(limit=limit)
  evaluators = {
      "latency": CodeEvaluator.latency(threshold_ms=latency_ms),
      "turn_count": CodeEvaluator.turn_count(max_turns=max_turns),
      "error_rate": CodeEvaluator.error_rate(max_error_rate=0.1),
  }

  print("\n=== Code-based evaluation (BQAA SDK) ===")
  for label, evaluator in evaluators.items():
    try:
      report = client.evaluate(evaluator, filters=filters)
    except Exception as e:  # pragma: no cover - surfaces config/permission issues
      print(f"[{label}] evaluation failed: {e}")
      continue
    print(f"\n[{label}]")
    print(report.summary())


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      "--limit", type=int, default=10, help="Max recent sessions to analyze."
  )
  parser.add_argument(
      "--latency-ms",
      type=float,
      default=10000.0,
      help="Average-latency budget (ms) for the latency evaluator.",
  )
  parser.add_argument(
      "--max-turns",
      type=int,
      default=10,
      help="Max acceptable turns for the turn_count evaluator.",
  )
  args = parser.parse_args()

  client = _make_client()
  print(
      f"Reading BQAA traces from {get_consumer_project()}."
      f"{get_bqaa_dataset_id()}.{get_bqaa_table_id()}\n"
  )
  reconstruct_latest_trace(client, args.limit)
  evaluate_sessions(client, args.limit, args.latency_ms, args.max_turns)


if __name__ == "__main__":
  main()
