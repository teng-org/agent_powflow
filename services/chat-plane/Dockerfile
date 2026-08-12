FROM python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/services/chat-plane

COPY services/chat-plane/requirements.txt /tmp/requirements.txt
RUN PIP_CONFIG_FILE=/dev/null PIP_REQUIRE_HASHES=0 pip install --index-url "$PIP_INDEX_URL" --no-cache-dir -r /tmp/requirements.txt

COPY services/chat-plane /app/services/chat-plane

CMD ["sh", "-c", "python -m runtime.schema_migrations && exec uvicorn api.app.main:app --host 0.0.0.0 --port 8092"]
