# ADR-001: Product Scope and Architecture Baseline (v1)

- Status: Accepted
- Date: 2026-02-09
- Decision Makers: Product + Architecture
- Scope: `agent_powflow` v1 enterprise-first baseline

## Context

The project targets a frontend-backend separated personal/team knowledge base + agent system with:

- Vue frontend (`web-app` + `web-admin`)
- Spring Boot control plane for user, tenant, governance, and authoritative state
- Python execution plane for AI pipelines and runtime
- Multi-tenant isolation, task orchestration, status synchronization, and plugin-based extensibility
- Agent Canvas for visual management, with executable DSL as the source of truth

To avoid overdesign in MVP while keeping enterprise viability, we need explicit scope decisions.

## Decision

1. User priority: enterprise-first, personal users remain as trial/self-host entry.
2. Private deployment: supported in v1, but full air-gapped offline mode is not mandatory for MVP.
3. Compliance: implement compliance capabilities first; formal certifications are out of MVP scope.
4. Multi-tenant isolation in MVP: accept L1 logical isolation (`tenant_id` enforced end-to-end).
5. Model strategy: architecture supports dual-stack; MVP runs OpenAI-compatible provider first.
6. Vector database: Qdrant as default production choice.
7. Broker: RabbitMQ as default production message broker.
8. Scale assumption for MVP: SME-level workload (not internet-scale high concurrency).
9. Canvas scope in MVP: DAG-only workflow, no loops/human-in-the-loop/state-machine complexity.
10. Monetization in v1: minimum license boundary (entitlement gating), no complex billing engine.

## Consequences

### Positive

- Keeps MVP complexity under control while preserving enterprise path.
- Reduces operational burden by standardizing Qdrant + RabbitMQ.
- Ensures migration-friendly agent design through DSL-first model.
- Enables commercial validation early with minimum licensing controls.

### Negative / Trade-offs

- Some enterprise leads may still request strict offline and formal compliance certificates.
- OpenAI-first may increase external dependency risk until local model stack is added.
- L1 isolation is sufficient for MVP but not enough for strict isolation customers.

### Follow-up

- Introduce L2/L3 tenant isolation options in post-MVP roadmap.
- Add local model runtime path (vLLM/Ollama) when customer or cost triggers appear.
- Extend compliance from capability-ready to certification-ready once target industry is fixed.
- Add ADRs for: vector DB selection details, broker topology, DSL schema versioning, and license model.
