"""BQAA Data Agent package.

Exposes both ``root_agent`` (the LlmAgent) and ``app`` (the ADK ``App`` wrapping
``root_agent`` with the BigQueryAgentAnalyticsPlugin registered as the trace
logging path). ADK tooling such as ``adk web`` / ``adk run`` discovers ``app``
so the plugin is active during local runs.
"""

try:
  from .agent import app, root_agent  # noqa: F401
except ImportError:
  from agent import app, root_agent  # noqa: F401
