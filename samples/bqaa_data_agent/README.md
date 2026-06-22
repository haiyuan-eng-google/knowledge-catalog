# BQAA Data Agent

The **BQAA Data Agent** is a variant of the [Chat with Data Agent](../chat_with_data_agent) that uses **[BigQuery Agent Analytics (BQAA)](https://adk.dev/integrations/bigquery-agent-analytics/)** as its trace logging path, and demonstrates how to consume those traces with the open-source **[BigQuery-Agent-Analytics-SDK](https://github.com/GoogleCloudPlatform/BigQuery-Agent-Analytics-SDK)**.

It keeps the same enterprise capabilities — Knowledge Catalog discovery (via an `AgentTool` sub-agent) plus BigQuery and Dataplex OneMCP tools — but instead of exporting OpenTelemetry spans to Cloud Trace / Cloud Logging, it registers the `BigQueryAgentAnalyticsPlugin` so that **every LLM request/response, tool call, agent transfer, and error is written as a row in a BigQuery events table**. Those rows are then a structured dataset you can query in SQL or analyze/evaluate with the BQAA SDK.

## The end-to-end loop

```
                 ┌──────────────────────────────────────┐
   user turn ──► │  BQAA Data Agent (ADK App)            │
                 │   • knowledge_catalog_discovery_agent │
                 │   • BigQuery OneMCP                    │
                 │   • Dataplex OneMCP                    │
                 │                                       │
                 │   plugins=[BigQueryAgentAnalyticsPlugin]
                 └───────────────┬──────────────────────┘
                                 │ Storage Write API (one row / event)
                                 ▼
                 {project}.{dataset}.agent_events  (BigQuery)
                                 │
                                 ▼
                 analyze.py  ──►  BigQuery-Agent-Analytics-SDK
                   • reconstruct & render a trace
                   • code-based evaluation (latency / turns / error rate)
```

## Architecture & Integration
- **BQAA as the trace path**: `agent.py` wraps `root_agent` in an ADK `App` with `plugins=[BigQueryAgentAnalyticsPlugin(...)]`. The plugin writes one row per agent event to `{GOOGLE_CLOUD_PROJECT}.{BIG_QUERY_DATASET_ID}.{BIG_QUERY_TABLE_ID}` (default dataset `agent_analytics`, table `agent_events`) via the BigQuery Storage Write API. The dataset must exist; the events table is auto-created on first write.
- **Agent Tool Delegation (`AgentTool`)**: Exposes the `knowledge_catalog_discovery_agent` as a tool for in-turn discovery.
- **BigQuery / Dataplex OneMCP**: Same OneMCP wiring as `chat_with_data_agent`, including the httpx event hook that refreshes the OAuth bearer token before each MCP request.
- **SDK consumption**: `analyze.py` uses the BQAA SDK `Client` to list/reconstruct traces and run deterministic `SystemEvaluator` metrics.

## Files
| File | Purpose |
|---|---|
| `agent.py` | Builds `root_agent` and the `App` with the BQAA plugin registered. |
| `knowledge_catalog_discovery_agent/` | Discovery sub-agent exposed via `AgentTool`. |
| `run_local.py` | Scripted local runner that drives a conversation and emits BQAA events. |
| `analyze.py` | Reads traces back with the BQAA SDK (reconstruct + evaluate). |
| `deploy.py` | Deploys to Vertex AI Agent Engine with the plugin attached. |
| `utils.py` | Resolves project + BQAA dataset/table/location from env. |
| `requirements.txt` | Runtime + deployment deps. |
| `requirements-analysis.txt` | Deps for `analyze.py` only (the BQAA SDK). |

## Prerequisites

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Application Default Credentials for local runs:
gcloud auth application-default login

# Required environment:
export GOOGLE_CLOUD_PROJECT="your-project-id"
export GOOGLE_CLOUD_LOCATION="us-central1"
export GOOGLE_GENAI_USE_VERTEXAI="True"

# Optional (defaults shown):
export BIG_QUERY_DATASET_ID="agent_analytics"
export BIG_QUERY_TABLE_ID="agent_events"
export BQ_LOCATION="US"

# Create the BQAA dataset once (table is auto-created by the plugin):
bq --location="${BQ_LOCATION}" mk --dataset "${GOOGLE_CLOUD_PROJECT}:${BIG_QUERY_DATASET_ID}"
```

### IAM for the trace path (BQAA)
The principal running the agent (your ADC locally, or the deployed service account) needs, in addition to the Knowledge Catalog / BigQuery / Dataplex data-access roles documented in `chat_with_data_agent`:
- **BigQuery Job User** (`roles/bigquery.jobUser`) — project level
- **BigQuery Data Editor** (`roles/bigquery.dataEditor`) — on the BQAA dataset (write events + auto-create table)

## Run locally

```bash
# Drive the built-in demo prompts (writes events to BQAA):
python3 run_local.py

# Or a single custom prompt:
python3 run_local.py "Which tables contain customer order data?"

# Or use the ADK dev UI (it discovers the App / plugin automatically):
adk web
```

Verify rows landed in BigQuery:

```sql
SELECT timestamp, event_type, agent, session_id
FROM `your-project-id.agent_analytics.agent_events`
ORDER BY timestamp DESC
LIMIT 20;
```

## Analyze the traces (BQAA SDK)

```bash
pip install -r requirements-analysis.txt
python3 analyze.py                                  # latest trace + default metrics
python3 analyze.py --limit 20 --latency-ms 8000 --max-turns 12
```

`analyze.py` will:
1. List recent session traces and **render the most recent one as a tree** (LLM calls, tool calls, transfers, errors).
2. Run **code-based evaluators** (`latency`, `turn_count`, `error_rate`) and print pass-rate reports.

This is only a slice of the SDK — it also supports LLM-as-Judge scoring, trajectory matching, drift detection, the Agent Context Graph, and more. See the [SDK docs](https://github.com/GoogleCloudPlatform/BigQuery-Agent-Analytics-SDK).

## Deploy to Vertex AI Agent Engine

`deploy.py` registers the plugin on the `AdkApp` (`plugins=[...]`) so the deployed runtime logs to BigQuery. The deployed service account needs the BigQuery roles above plus the OneMCP data-access roles.

```bash
export GOOGLE_CLOUD_PROJECT="your-project-id"
export GOOGLE_CLOUD_LOCATION="us-central1"
export STAGING_BUCKET="gs://your-staging-bucket"   # optional; defaults to gs://{project}-adk-staging
export SERVICE_ACCOUNT="your-runtime-sa@your-project.iam.gserviceaccount.com"  # optional
export BIG_QUERY_DATASET_ID="agent_analytics"       # persisted to the container

# Create:
DEPLOY_ACTION="create" python3 deploy.py

# Update:
DEPLOY_ACTION="update" RESOURCE_ID="projects/.../reasoningEngines/123" python3 deploy.py

# Delete:
DEPLOY_ACTION="delete" RESOURCE_ID="projects/.../reasoningEngines/123" python3 deploy.py
```

> `enable_tracing` is left `False` in `deploy.py` because BQAA — not Cloud Trace — is this sample's telemetry sink. Set it `True` if you additionally want OpenTelemetry spans in Cloud Trace.

## How it differs from `chat_with_data_agent`
| | chat_with_data_agent | bqaa_data_agent |
|---|---|---|
| Trace path | OTEL → Cloud Trace + Cloud Logging | BQAA plugin → BigQuery events table |
| Wiring | `AdkApp(agent=root_agent, enable_tracing=True)` + OTEL env vars | `App(root_agent, plugins=[BigQueryAgentAnalyticsPlugin])` / `AdkApp(..., plugins=[...])` |
| Downstream analysis | Cloud Trace / Logs Explorer | SQL + BigQuery-Agent-Analytics-SDK (`analyze.py`) |
