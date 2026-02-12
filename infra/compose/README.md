# compose

## Files

- `docker-compose.dev.yml`: infrastructure dependencies (PostgreSQL/Mongo/RabbitMQ/MinIO/Qdrant).
- `docker-compose.stack.yml`: full local stack (infra + control-plane + execution-api + execution-worker).

## Run

Infra only:

```bash
docker compose -f infra/compose/docker-compose.dev.yml up -d
```

Full stack:

```bash
docker compose -f infra/compose/docker-compose.stack.yml up -d --build
```
