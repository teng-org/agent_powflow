def publish_control_run_dead_letter(celery_app, exc, task_id: str, args) -> None:
    payload = args[0] if args and isinstance(args[0], dict) else {}
    run_id = str(payload.get("run_id") or "unknown")
    dead_letter = {
        "run_id": run_id,
        "tenant_id": str(payload.get("tenant_id") or ""),
        "failed_task_id": str(task_id),
        "error": str(exc)[:2000],
        "source_service": "execution-worker",
        "source_queue": "control_run_queue",
        "payload": payload,
    }
    celery_app.send_task(
        "agentpowflow.control_run.dead_letter",
        args=[dead_letter],
        queue="control_run_dead_letter",
        exchange="control_run_dead_letter",
        routing_key="control_run.dead",
        task_id=f"control-run-dead-{run_id}-{task_id}",
        ignore_result=True,
    )
