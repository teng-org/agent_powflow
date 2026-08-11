from celery import Celery
from celery.signals import worker_ready
from kombu import Exchange, Queue
import os

from models.api_pool import api_pool_manager
from retrieval.schema_migrations import require_database_current

broker_url = os.getenv("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
backend_url = os.getenv("CELERY_RESULT_BACKEND", "rpc://")

require_database_current()

try:
    api_pool_manager.bootstrap()
except Exception:
    # API pool bootstrap is best-effort here; runtime calls fail fast if no DB endpoint is configured.
    pass

celery_app = Celery(
    "execution-workers",
    broker=broker_url,
    backend=backend_url,
    include=["workers.app.tasks"],
)
celery_app.conf.task_default_queue = "agent_queue"
control_run_exchange = Exchange("control_run", type="direct", durable=True)
control_run_dead_exchange = Exchange("control_run_dead_letter", type="direct", durable=True)
celery_app.conf.task_queues = (
    Queue(
        "control_run_queue",
        exchange=control_run_exchange,
        routing_key="control_run.execute",
        durable=True,
        queue_arguments={
            "x-dead-letter-exchange": "control_run_dead_letter",
            "x-dead-letter-routing-key": "control_run.dead",
        },
    ),
    Queue(
        "control_run_dead_letter",
        exchange=control_run_dead_exchange,
        routing_key="control_run.dead",
        durable=True,
    ),
)
celery_app.conf.task_routes = {
    "app.tasks.parse_document": {"queue": "parse_queue"},
    "app.tasks.embed_document": {"queue": "embed_queue"},
    "app.tasks.commit_chunk_drafts": {"queue": "embed_queue"},
    "app.tasks.index_document": {"queue": "index_queue"},
    "app.tasks.repair_cross_store": {"queue": "agent_queue"},
    "app.tasks.run_agent": {"queue": "agent_queue"},
    "app.tasks.execute_control_run": {
        "queue": "control_run_queue",
        "exchange": "control_run",
        "routing_key": "control_run.execute",
    },
    "agentpowflow.control_run.dead_letter": {
        "queue": "control_run_dead_letter",
        "exchange": "control_run_dead_letter",
        "routing_key": "control_run.dead",
    },
}
celery_app.conf.task_acks_late = True
celery_app.conf.worker_prefetch_multiplier = 1
celery_app.conf.broker_connection_retry_on_startup = True


@worker_ready.connect
def declare_control_run_dead_letter_queue(**_kwargs) -> None:
    # Declare explicitly so publishers and broker dead-letter routing both have
    # a durable destination before the first control-run failure occurs.
    with celery_app.connection_for_write() as connection:
        celery_app.amqp.queues["control_run_dead_letter"].bind(connection).declare()
