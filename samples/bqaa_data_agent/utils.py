"""Utility functions for the BQAA Data Agent.

These helpers resolve the consumer project plus the BigQuery Agent Analytics
(BQAA) destination (dataset / table / location) from environment variables so
the same configuration is shared by ``agent.py`` (runtime trace logging),
``deploy.py`` (Agent Engine deployment), and ``analyze.py`` (SDK consumption).
"""

import os

# Default BigQuery dataset that the BigQueryAgentAnalyticsPlugin writes to. The
# plugin auto-creates the events table inside this dataset, but the dataset
# itself must already exist (`bq mk --dataset PROJECT:agent_analytics`).
DEFAULT_BQAA_DATASET_ID = "agent_analytics"
# Default events table name used by the Python plugin.
DEFAULT_BQAA_TABLE_ID = "agent_events"
# BigQuery location of the dataset above. Keep in sync with where the dataset
# was created; this is distinct from the Vertex AI location used for the model.
DEFAULT_BQAA_LOCATION = "US"


def get_consumer_project() -> str:
  """Returns the consumer project ID from the environment.

  Raises:
      ValueError: If the GOOGLE_CLOUD_PROJECT environment variable is not set.
  """
  consumer_project = os.environ.get("GOOGLE_CLOUD_PROJECT")
  if not consumer_project:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable is required.")
  return consumer_project


def get_bqaa_dataset_id() -> str:
  """Returns the BigQuery dataset that BQAA traces are written to/read from."""
  return os.environ.get("BIG_QUERY_DATASET_ID", DEFAULT_BQAA_DATASET_ID)


def get_bqaa_table_id() -> str:
  """Returns the BigQuery events table name used by the BQAA plugin."""
  return os.environ.get("BIG_QUERY_TABLE_ID", DEFAULT_BQAA_TABLE_ID)


def get_bqaa_location() -> str:
  """Returns the BigQuery location of the BQAA dataset."""
  return os.environ.get("BQ_LOCATION", DEFAULT_BQAA_LOCATION)
