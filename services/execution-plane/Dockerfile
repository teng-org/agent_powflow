FROM python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/services/execution-plane

COPY services/execution-plane/requirements.txt /tmp/requirements.txt
RUN PIP_CONFIG_FILE=/dev/null PIP_REQUIRE_HASHES=0 pip install --index-url "$PIP_INDEX_URL" --no-cache-dir -r /tmp/requirements.txt

RUN groupadd --gid 10001 agentpowflow \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin agentpowflow

COPY --chown=10001:10001 services/execution-plane /app/services/execution-plane

USER 10001:10001

CMD ["sh", "-c", "python -m retrieval.schema_migrations && exec uvicorn api.app.main:app --host 0.0.0.0 --port 8090 --workers ${EXECUTION_API_WORKERS:-2}"]
