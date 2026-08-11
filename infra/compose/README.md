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
docker compose --env-file config/stack.generated.env -f infra/compose/docker-compose.stack.yml up -d --build
```

## Notes

- If `auth.docker.io` is unstable behind proxy, BuildKit metadata resolve may fail during parallel build.
- Prefer `infra/scripts/start-stack.ps1 -Build`; it generates and loads `config/stack.generated.env` automatically:
  - pre-pulls base build images with retry
  - limits compose parallelism during build to reduce transient token-fetch failures
