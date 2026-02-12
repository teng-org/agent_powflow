FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app/services/execution-plane

COPY services/execution-plane/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY services/execution-plane /app/services/execution-plane

CMD ["uvicorn", "api.app.main:app", "--host", "0.0.0.0", "--port", "8090"]

