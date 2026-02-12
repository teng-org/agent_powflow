# Control Plane

## Run

```bash
mvn spring-boot:run
```

## Storage Baseline

- Authoritative business data: PostgreSQL
- Optional event/big-payload store: MongoDB (execution/event side)

## Endpoints

- `GET /api/v1/system/health`
- `GET /api/v1/system/context`
