# ADR-004：执行平面队列拓扑与可靠性策略（v1）

- Status: Accepted
- Date: 2026-02-09
- Decision Makers: Product + Architecture
- Scope: `agent_powflow` v1 execution plane
- Related:
  - `docs/design/current-phase-construction-checklist-v1.zh-CN.md`
  - `docs/decisions/ADR-003-tenant-isolation-v1.md`

## Context

Execution Plane uses Celery + RabbitMQ to run document ingest and agent workflows.
Without explicit queue and reliability contracts, the system risks:

- duplicate execution
- silent message loss
- unbounded retries
- ambiguous timeout behavior
- unsafe dead-letter replay

This ADR defines fixed v1 runtime and queue rules.

## Decision

## 1) Queue topology (fixed in v1)

Required queues:

- `parse_queue`: document parsing/OCR/text extraction
- `embed_queue`: embedding generation
- `index_queue`: vector upsert/index maintenance
- `agent_queue`: workflow/run execution
- `events_queue` (optional): async event fan-out

Dead-letter queues:

- `parse_dlq`
- `embed_dlq`
- `index_dlq`
- `agent_dlq`

Routing rule:

- `DOC_INGEST` -> `parse_queue`
- parse success -> `embed_queue` -> `index_queue`
- `RUN_EXECUTE` -> `agent_queue`

## 2) Delivery semantics

- Broker semantics: at-least-once delivery.
- Consumers must be idempotent.
- Ack only after durable state write or guaranteed event callback.
- No auto-ack for business tasks.

## 3) Idempotency contract

Each executable unit must have deterministic idempotency key:

- Task key: `tenant_id + task_id + stage + attempt_window`
- Run node key: `tenant_id + run_id + node_id + input_hash`

Storage rules:

- idempotency records are persisted in Mongo with TTL.
- repeated key within TTL must return previous result or no-op.
- workers cannot generate tenant context; they must use passed context.

## 4) Retry / timeout policy (fixed, non-configurable in v1)

Default retries:

- parse: 2
- embed: 2
- index: 2
- agent: 1
- tool node inside run: 2 (exponential backoff)

Default timeout:

- parse: 120s
- embed: 90s
- index: 60s
- agent run hard timeout: 180s
- single tool call timeout: 30s

Failure behavior:

- node failure defaults to `FAIL_RUN` (except final output formatting)
- insufficient evidence does not block generation, but must mark `low_evidence=true`
- timeout counts as failure and consumes retry budget

## 5) DLQ and replay guardrails

- Any message exceeding retry budget moves to corresponding DLQ.
- Replay can only be triggered by Control Plane admin API.
- Direct worker-side replay is forbidden.
- Replay must create audit log with `tenant_id/actor_id/trace_id/source_service`.

Required replay API (control plane):

- `POST /api/v1/admin/dlq/replay/{queue}/{message_id}`
- `POST /api/v1/admin/dlq/discard/{queue}/{message_id}`

## 6) Ordering and concurrency

- Per `run_id` execution is serialized unless DSL marks branch as parallel.
- Per `doc_id` ingest stages must preserve parse -> embed -> index order.
- Worker concurrency is queue-scoped and capacity-limited.

## 7) Observability contract

All queue handlers must emit structured logs/metrics with:

- `tenant_id`
- `actor_id`
- `trace_id`
- `source_service`
- `task_id` or `run_id`
- `queue_name`
- `attempt`
- `duration_ms`
- `result_status`

Minimum metrics:

- queue depth
- consumer lag
- success/failure rate
- retry count
- dlq inflow rate
- p95 duration per task type

## Consequences

Positive:

- predictable failure behavior
- bounded retry and timeout cost
- safer replay operations
- traceable queue execution path

Trade-offs:

- less flexibility in v1 (fixed policy)
- some valid long-running jobs may need manual tuning in future

## Guardrails

1. No queue/task handler can merge without idempotency key strategy.
2. No worker code may bypass Control Plane for DLQ replay.
3. Any new queue type requires ADR update.
4. Integration tests must cover retry -> DLQ -> replay flow.
