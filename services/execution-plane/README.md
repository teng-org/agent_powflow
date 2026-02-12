# Execution Plane

## Install

```bash
pip install -r requirements.txt
```

## Run API

```bash
uvicorn api.app.main:app --reload --port 8090
```

## Run Worker

```bash
celery -A workers.app.celery_app:celery_app worker -Q parse_queue,embed_queue,index_queue,agent_queue -l info
```

## Required Services

- PostgreSQL (API pool persistence + retrieval metadata/state)
- Qdrant (vector retrieval)
- RabbitMQ (Celery broker)

## Important Env Vars

- `POSTGRES_DSN` (or `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD`)
- `QDRANT_URL`
- `QDRANT_API_KEY` (optional)
- `EXECUTION_ADMIN_TOKEN` (admin API auth token)
- `SILICONFLOW_API_KEY` (embedding API token, optional fallback to local embedding)
