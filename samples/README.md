# Samples

The samples demonstrate the use of Knowledge Catalog to manage metadata and
context to power agents.

## Discovery

Demonstrates building a search and discovery agent building on top of the
Search APIs offered by the catalog.

## Enrichment

Demonstrates an enrichment agent that can generate and enrich documentation
for assets managed in the catalog.

## BQAA Data Agent

[`bqaa_data_agent/`](bqaa_data_agent/) — a variant of `chat_with_data_agent`
that uses **BigQuery Agent Analytics (BQAA)** as its trace logging path: every
LLM call, tool call, and agent transfer is written to a BigQuery events table,
which an included `analyze.py` reads back with the
[BigQuery-Agent-Analytics-SDK](https://github.com/GoogleCloudPlatform/BigQuery-Agent-Analytics-SDK)
to reconstruct traces and run code-based evaluators.
