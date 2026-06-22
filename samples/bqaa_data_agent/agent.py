"""BQAA Data Agent: Chat-with-data over OneMCP, traced to BigQuery via BQAA.

This mirrors the ``chat_with_data_agent`` sample (Knowledge Catalog discovery
sub-agent + BigQuery & Dataplex OneMCP tools) but replaces the OpenTelemetry
Cloud Trace / Cloud Logging path with the **BigQuery Agent Analytics (BQAA)
plugin** as the trace logging path. Every LLM request/response, tool call, and
agent transfer is written to a BigQuery events table for downstream
observability and evaluation (see ``analyze.py``).
"""

import asyncio
import os
import sys

# Ensure current directory is in sys.path so discovery_agent can be imported.
curr_dir = os.path.dirname(os.path.abspath(__file__))
if curr_dir not in sys.path:
  sys.path.insert(0, curr_dir)

from google.adk.agents.llm_agent import LlmAgent
from google.adk.apps import App
from google.adk.models import google_llm
from google.adk.plugins.bigquery_agent_analytics_plugin import (
    BigQueryAgentAnalyticsPlugin,
)
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StreamableHTTPConnectionParams,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
import google.auth
import google.auth.transport.requests
import httpx

try:
  from .knowledge_catalog_discovery_agent.agent import (
      root_agent as discovery_agent,
  )
  from .utils import (
      get_bqaa_dataset_id,
      get_bqaa_location,
      get_bqaa_table_id,
      get_consumer_project,
  )
except ImportError:
  from knowledge_catalog_discovery_agent.agent import (
      root_agent as discovery_agent,
  )
  from utils import (
      get_bqaa_dataset_id,
      get_bqaa_location,
      get_bqaa_table_id,
      get_consumer_project,
  )

consumer_project = get_consumer_project()
GEMINI_MODEL = f"projects/{consumer_project}/locations/global/publishers/google/models/gemini-2.5-flash"

BIGQUERY_MCP_ENDPOINT = "https://bigquery.googleapis.com/mcp"
DATAPLEX_MCP_ENDPOINT = "https://dataplex.googleapis.com/mcp"
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# Credentials object stays in module scope; the httpx event hook below calls
# .refresh() on it lazily before each MCP request so the Bearer token never
# goes stale mid-session.
credentials, _ = google.auth.default(
    scopes=[CLOUD_PLATFORM_SCOPE], quota_project_id=consumer_project
)
credentials.refresh(google.auth.transport.requests.Request())


async def _refresh_auth_on_request(request: httpx.Request) -> None:
  """httpx async event hook: ensure the Authorization header is a live token.

  Without this hook, the Bearer token captured at module-import time expires
  after ~60 minutes and every subsequent MCP call returns 401 Unauthorized
  (surfaced by the ADK MCP layer as `MCP session connection lost`).
  """
  if not credentials.valid:
    # google-auth refresh is sync; offload to a thread to avoid blocking loop.
    await asyncio.to_thread(
        credentials.refresh, google.auth.transport.requests.Request()
    )
  request.headers["Authorization"] = f"Bearer {credentials.token}"
  # X-Goog-User-Project is set via static headers below; backfill here only
  # if a downstream caller stripped it.
  if "X-Goog-User-Project" not in request.headers:
    request.headers["X-Goog-User-Project"] = consumer_project


def make_mcp_http_client(
    headers: dict | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
  """httpx client factory passed to McpToolset.

  Wraps the default MCP client with the refresh hook so every request gets a
  live OAuth Bearer token regardless of session age.
  """
  return httpx.AsyncClient(
      headers=headers,
      timeout=timeout if timeout is not None else httpx.Timeout(30.0),
      auth=auth,
      follow_redirects=True,
      event_hooks={"request": [_refresh_auth_on_request]},
  )


# Static headers carry only non-rotating values. The Bearer token is injected
# by _refresh_auth_on_request right before each request.
static_headers = {"X-Goog-User-Project": consumer_project}


# Path to the skill file relative to the agent.py location.
SKILL_FILE_PATH = os.path.join(os.path.dirname(__file__), "SKILL.md")


def load_instruction(project_id: str) -> str:
  """Loads the agent instruction from the SKILL.md file."""
  try:
    with open(SKILL_FILE_PATH, "r") as f:
      content = f.read()
  except FileNotFoundError:
    content = (
        "You are the BQAA Data Agent. Discover data assets using the discovery"
        " sub-agent and explore/query data using BigQuery and Dataplex OneMCP"
        " tools."
    )
  return (
      content
      + f"\n\nUse Consumer Project ID: {project_id} for billing or running"
      " queries"
  )


bigquery_mcp_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=BIGQUERY_MCP_ENDPOINT,
        headers=static_headers,
        httpx_client_factory=make_mcp_http_client,
    ),
)

dataplex_mcp_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=DATAPLEX_MCP_ENDPOINT,
        headers=static_headers,
        httpx_client_factory=make_mcp_http_client,
    ),
    tool_filter=lambda tool, ctx=None: tool.name != "search_entries",
)

agent_tools = [
    AgentTool(discovery_agent),
    bigquery_mcp_toolset,
    dataplex_mcp_toolset,
]

root_agent = LlmAgent(
    model=google_llm.Gemini(model=GEMINI_MODEL),
    name="bqaa_data_agent",
    description=(
        "An intelligent agent that discovers data assets using the Knowledge"
        " Catalog Discovery Agent tool and answers inquiries using BigQuery and"
        " Dataplex OneMCP tools, with every step traced to BigQuery via the"
        " BigQuery Agent Analytics plugin."
    ),
    instruction=load_instruction(consumer_project),
    tools=agent_tools,
)


def build_bqaa_plugin() -> BigQueryAgentAnalyticsPlugin:
  """Builds the BigQuery Agent Analytics plugin used as the trace logging path.

  The plugin writes one row per agent event (LLM_REQUEST, LLM_RESPONSE,
  TOOL_STARTED/COMPLETED, agent transfers, errors, ...) to
  ``{project}.{dataset}.{table}`` via the BigQuery Storage Write API. The
  dataset must already exist; the events table is auto-created on first write.
  """
  return BigQueryAgentAnalyticsPlugin(
      project_id=consumer_project,
      dataset_id=get_bqaa_dataset_id(),
      table_id=get_bqaa_table_id(),
      location=get_bqaa_location(),
  )


# The App registers the BQAA plugin application-wide. ADK runners (and `adk web`
# / `adk run`) execute through this App so the plugin observes every event.
app = App(
    name="bqaa_data_agent",
    root_agent=root_agent,
    plugins=[build_bqaa_plugin()],
)
