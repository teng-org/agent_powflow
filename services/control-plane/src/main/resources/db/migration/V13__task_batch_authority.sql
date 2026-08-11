ALTER TABLE task_batches
    ADD COLUMN IF NOT EXISTS cancelled_items INTEGER NOT NULL DEFAULT 0;

ALTER TABLE task_batches
    ADD CONSTRAINT chk_task_batches_counts_nonnegative
    CHECK (
        total_items >= 0 AND succeeded_items >= 0 AND failed_items >= 0
        AND running_items >= 0 AND cancelled_items >= 0
    ) NOT VALID;

ALTER TABLE task_batches
    VALIDATE CONSTRAINT chk_task_batches_counts_nonnegative;

CREATE INDEX IF NOT EXISTS idx_task_batches_parent_task
    ON task_batches (tenant_id, parent_task_id);
