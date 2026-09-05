# ADR 0005: Docker-first development and enterprise deployment seams

- Status: Accepted
- Date: 2026-09-05

## Context

The first implementation must be easy to run locally without baking local-only
assumptions into persistence, networking, or identity. A future hosted enterprise
service will need independent scaling, managed infrastructure, observability,
upgrades, and stronger tenancy controls.

## Decision

Docker Compose is the reference local environment. A fresh clone must start with
documented commands, explicit configuration, health checks, persistent named
volumes, deterministic migrations, and development-safe default secrets.

Define deployment seams from the beginning:

- stateless MCP/API and control-plane processes;
- replaceable metadata, blob, and search adapters with durable local defaults;
- a separately deployable web-artifact renderer and sandbox origin;
- explicit background-job interfaces for extraction, indexing, and cleanup;
- environment-based configuration with startup validation;
- structured logs, metrics, traces, health, and readiness endpoints; and
- forward-only, repeatable schema migrations and versioned service contracts.

Production deployments may replace Compose with an orchestrator and managed
services, but must preserve the same externally visible MCP, artifact, version,
provenance, and sandbox contracts. Enterprise deployment concerns such as SSO,
key management, backups, retention, regional placement, and audit export can be
added behind explicit interfaces rather than by forking the core.

## Consequences

- Local development resembles the topology of a hosted deployment.
- Extra service boundaries create some early operational overhead.
- Storage and identity implementations must be selected through interfaces and
  tested for conformance.
- Compose is a development and evaluation target, not a claim of production
  orchestration readiness.

## Non-goals

- Choosing a production cloud, orchestrator, or managed-service vendor now.
- Shipping the first milestone as a multi-region enterprise service.
- Hiding all operational differences behind one oversized application process.
- Building a marketplace or customer-specific extension framework.

## Open questions

- Which local storage services minimize setup while exercising production-like
  failure modes?
- What backup, restore, and disaster-recovery objectives will hosted deployments
  require?
- Which components need independent scaling first under realistic workloads?
