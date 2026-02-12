from celery import Celery
import os

from models.api_pool import api_pool_manager

broker_url = os.getenv("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
backend_url = os.getenv("CELERY_RESULT_BACKEND", "rpc://")

api_pool_config = os.getenv("API_POOL_DEFAULT_CONFIG", "/app/services/execution-plane/config/api_pool.default.json")
try:
    api_pool_manager.bootstrap(os.path.abspath(api_pool_config))
except Exception:
    # API pool bootstrap is best-effort here; runtime calls may still fail fast in api_strict mode.
    pass

celery_app = Celery(
    "execution-workers",
    broker=broker_url,
    backend=backend_url,
    include=["workers.app.tasks"],
)
celery_app.conf.task_default_queue = "agent_queue"
celery_app.conf.task_routes = {
    "app.tasks.parse_document": {"queue": "parse_queue"},
    "app.tasks.embed_document": {"queue": "embed_queue"},
    "app.tasks.commit_chunk_drafts": {"queue": "embed_queue"},
    "app.tasks.index_document": {"queue": "index_queue"},
    "app.tasks.run_agent": {"queue": "agent_queue"},
}
celery_app.conf.task_acks_late = True
celery_app.conf.worker_prefetch_multiplier = 1
