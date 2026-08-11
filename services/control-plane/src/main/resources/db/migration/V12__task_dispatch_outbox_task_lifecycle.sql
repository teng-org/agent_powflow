-- A dispatch command has no lifecycle after its authoritative Task is removed.
-- Clean pre-constraint orphans, then enforce this invariant for every future delete.
DELETE FROM task_dispatch_outbox outbox
WHERE NOT EXISTS (
    SELECT 1 FROM tasks task WHERE task.task_id = outbox.task_id
);

ALTER TABLE task_dispatch_outbox
    ADD CONSTRAINT fk_task_dispatch_outbox_task
    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE;
