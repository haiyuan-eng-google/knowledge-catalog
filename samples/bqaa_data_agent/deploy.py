"""Deployment script for the BQAA Data Agent (Vertex AI Agent Engine).

Differs from the chat_with_data_agent deploy in one important way: instead of
configuring OpenTelemetry Cloud Trace / Cloud Logging exporters, it registers
the BigQuery Agent Analytics plugin on the ``AdkApp`` via ``plugins=[...]`` so
the deployed runtime logs agent events directly to BigQuery.

The deployed service account therefore needs BigQuery write access in addition
to the data-access roles required by the OneMCP tools (see README).
"""

import logging
import os
import sys

from dotenv import load_dotenv
from google.api_core import exceptions as google_exceptions
from google.cloud import storage
import vertexai
from vertexai import agent_engines
from vertexai.preview.reasoning_engines import AdkApp

# Ensure current directory is in sys.path so agent can be imported.
agent_dir = os.path.dirname(os.path.abspath(__file__))
if agent_dir not in sys.path:
    sys.path.insert(0, agent_dir)

from agent import build_bqaa_plugin, root_agent
from utils import get_bqaa_dataset_id, get_bqaa_location, get_bqaa_table_id

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_DEFAULT_REQUIREMENTS = [
    "google-adk>=1.27.0",
    "google-cloud-dataplex",
    "mcp",
    "google-api-core",
    "google-cloud-aiplatform[agent_engines]",
    "python-dotenv",
    "google-cloud-bigquery",
    "google-cloud-bigquery-storage",
]


def _read_requirements() -> list[str]:
    """Reads requirements.txt next to this file, with a sane fallback."""
    req_path = os.path.join(agent_dir, "requirements.txt")
    if os.path.exists(req_path):
        with open(req_path, "r") as f:
            reqs = [
                line.strip()
                for line in f
                if line.strip() and not line.startswith("#")
            ]
        if reqs:
            return reqs
    return list(_DEFAULT_REQUIREMENTS)


def _bqaa_env() -> dict[str, str]:
    """Env vars persisted to the container so the BQAA plugin/agent resolve the
    same destination at runtime as at deploy time."""
    return {
        "GOOGLE_GENAI_USE_VERTEXAI": os.getenv(
            "GOOGLE_GENAI_USE_VERTEXAI", "True"
        ),
        "BIG_QUERY_DATASET_ID": get_bqaa_dataset_id(),
        "BIG_QUERY_TABLE_ID": get_bqaa_table_id(),
        "BQ_LOCATION": get_bqaa_location(),
    }


def _build_app() -> AdkApp:
    """Builds the AdkApp with the BQAA plugin as the trace logging path.

    ``enable_tracing`` is left False because BQAA — not Cloud Trace — is the
    telemetry sink for this sample. Set it True if you additionally want
    OpenTelemetry spans in Cloud Trace.
    """
    logger.info(
        "Initializing AdkApp with root_agent + BigQueryAgentAnalyticsPlugin"
        " (dataset=%s, table=%s)...",
        get_bqaa_dataset_id(),
        get_bqaa_table_id(),
    )
    return AdkApp(
        agent=root_agent,
        plugins=[build_bqaa_plugin()],
        enable_tracing=False,
    )


def setup_staging_bucket(
    project_id: str, location: str, bucket_name: str
) -> str:
    """Checks if the staging bucket exists and creates it if not."""
    storage_client = storage.Client(project=project_id)
    try:
        bucket = storage_client.lookup_bucket(bucket_name)
        if bucket:
            logger.info("Staging bucket gs://%s already exists.", bucket_name)
        else:
            logger.info(
                "Staging bucket gs://%s not found. Creating...", bucket_name
            )
            new_bucket = storage_client.create_bucket(
                bucket_name, project=project_id, location=location
            )
            logger.info(
                "Successfully created staging bucket gs://%s in %s.",
                new_bucket.name,
                location,
            )
            new_bucket.iam_configuration.uniform_bucket_level_access_enabled = True
            new_bucket.patch()
            logger.info(
                "Enabled uniform bucket-level access for gs://%s.",
                new_bucket.name,
            )
    except google_exceptions.Forbidden as e:
        logger.error(
            "Permission denied for bucket gs://%s. Ensure the service account"
            " has 'Storage Admin'. Error: %s",
            bucket_name,
            e,
        )
        raise
    except google_exceptions.Conflict as e:
        logger.warning("Bucket gs://%s likely already exists. Error: %s", bucket_name, e)
    except google_exceptions.ClientError as e:
        logger.error("Failed to create/access bucket gs://%s. Error: %s", bucket_name, e)
        raise

    return f"gs://{bucket_name}"


def _resource_name(remote_agent) -> str:
    if hasattr(remote_agent, "resource_name"):
        return remote_agent.resource_name
    if hasattr(remote_agent, "api_resource") and hasattr(
        remote_agent.api_resource, "name"
    ):
        return remote_agent.api_resource.name
    return str(remote_agent)


def create(service_account: str | None = None) -> None:
    """Creates and deploys the BQAA Data Agent via AdkApp configuration."""
    os.chdir(agent_dir)
    logger.info("Deploying BQAA Data Agent via AdkApp on Vertex AI Agent Engine...")

    app = _build_app()
    logger.info("Creating remote agent engine with service account %s...", service_account)
    remote_agent = agent_engines.create(
        app,
        display_name="BQAA Data Agent",
        description=(
            "An intelligent assistant that chats with enterprise data via"
            " Knowledge Catalog discovery + BigQuery/Dataplex OneMCP, logging"
            " every step to BigQuery via the BigQuery Agent Analytics plugin."
        ),
        requirements=_read_requirements(),
        extra_packages=["."],
        env_vars=_bqaa_env(),
        service_account=service_account,
    )
    logger.info("Successfully deployed agent: %s", _resource_name(remote_agent))
    print(f"\nSuccessfully deployed agent: {_resource_name(remote_agent)}")


def update(resource_id: str, service_account: str | None = None) -> None:
    """Updates the specified agent via AdkApp configuration."""
    os.chdir(agent_dir)
    logger.info("Updating BQAA Data Agent %s via AdkApp...", resource_id)

    app = _build_app()
    logger.info("Updating remote agent engine with service account %s...", service_account)
    remote_agent = agent_engines.update(
        resource_id,
        agent_engine=app,
        requirements=_read_requirements(),
        extra_packages=["."],
        env_vars=_bqaa_env(),
        service_account=service_account,
    )
    logger.info("Successfully updated agent: %s", _resource_name(remote_agent))
    print(f"\nSuccessfully updated agent: {_resource_name(remote_agent)}")


def delete(resource_id: str) -> None:
    """Deletes the specified agent."""
    logger.info("Attempting to delete agent: %s", resource_id)
    try:
        remote_agent = agent_engines.get(resource_id)
        if hasattr(remote_agent, "delete"):
            try:
                remote_agent.delete(force=True)
            except TypeError:
                remote_agent.delete()
        logger.info("Successfully deleted remote agent: %s", resource_id)
        print(f"\nSuccessfully deleted agent: {resource_id}")
    except google_exceptions.NotFound:
        logger.error("Agent with resource ID %s not found.", resource_id)
        print(f"\nAgent not found: {resource_id}")
    except Exception as e:
        logger.error("Error while deleting agent %s: %s", resource_id, e)
        print(f"\nError deleting agent {resource_id}: {e}")


def main() -> None:
    load_dotenv()

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    default_bucket_name = f"{project_id}-adk-staging" if project_id else None
    bucket_name = os.getenv("GOOGLE_CLOUD_STORAGE_BUCKET") or os.getenv(
        "STAGING_BUCKET", default_bucket_name
    )
    service_account = os.getenv("SERVICE_ACCOUNT")

    action = os.getenv("DEPLOY_ACTION", "create").lower()
    resource_id = os.getenv("RESOURCE_ID")

    logger.info("Using PROJECT: %s", project_id)
    logger.info("Using LOCATION: %s", location)
    logger.info("Using BUCKET NAME: %s", bucket_name)
    logger.info("Using SERVICE ACCOUNT: %s", service_account)
    logger.info("Using BQAA DATASET: %s", get_bqaa_dataset_id())
    logger.info("Using ACTION: %s", action)

    if not project_id:
        print("\nError: Missing required GCP Project ID.")
        print("Set GOOGLE_CLOUD_PROJECT or PROJECT_ID environment variable.")
        return
    if not location:
        print("\nError: Missing required GCP Location.")
        return
    if not bucket_name and action in ("create", "update"):
        print(
            f"\nError: Missing required GCS Bucket Name for staging when"
            f" DEPLOY_ACTION={action}."
        )
        return
    if action in ("delete", "update") and not resource_id:
        print(
            f"\nError: RESOURCE_ID environment variable is required when"
            f" DEPLOY_ACTION={action}."
        )
        return

    try:
        staging_bucket_uri = None
        if action in ("create", "update"):
            staging_bucket_uri = setup_staging_bucket(
                project_id, location, bucket_name
            )

        vertexai.init(
            project=project_id,
            location=location,
            staging_bucket=staging_bucket_uri,
        )

        if action == "create":
            create(service_account)
        elif action == "update":
            update(resource_id, service_account)
        elif action == "delete":
            delete(resource_id)
        else:
            print(
                f"\nError: Unknown DEPLOY_ACTION '{action}'. Supported actions:"
                " create, update, delete."
            )

    except google_exceptions.Forbidden as e:
        print(f"\nPermission Error: {e}")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        logger.exception("Unhandled exception in main:")


if __name__ == "__main__":
    main()
