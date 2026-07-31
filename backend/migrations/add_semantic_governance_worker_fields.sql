-- Add worker lease/cancel/retry fields for semantic governance runs.
-- Kept idempotent without relying on ALTER TABLE ADD COLUMN IF NOT EXISTS.

DROP PROCEDURE IF EXISTS add_semantic_governance_run_column;
DELIMITER //
CREATE PROCEDURE add_semantic_governance_run_column(
    IN p_column VARCHAR(64),
    IN p_definition TEXT
)
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'semantic_governance_runs'
          AND COLUMN_NAME = p_column
    ) THEN
        SET @ddl = CONCAT('ALTER TABLE `semantic_governance_runs` ADD COLUMN `', p_column, '` ', p_definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END//
DELIMITER ;

CALL add_semantic_governance_run_column('worker_id', 'VARCHAR(128) NULL');
CALL add_semantic_governance_run_column('run_token', 'VARCHAR(64) NULL');
CALL add_semantic_governance_run_column('lease_expires_at', 'DATETIME NULL');
CALL add_semantic_governance_run_column('heartbeat_at', 'DATETIME NULL');
CALL add_semantic_governance_run_column('attempt', 'INT NOT NULL DEFAULT 0');
CALL add_semantic_governance_run_column('max_attempts', 'INT NOT NULL DEFAULT 2');
CALL add_semantic_governance_run_column('cancel_requested_at', 'DATETIME NULL');

DROP PROCEDURE IF EXISTS add_semantic_governance_run_column;

SET @idx_exists := (
    SELECT COUNT(1)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'semantic_governance_runs'
      AND index_name = 'ix_semantic_governance_run_status_lease'
);
SET @idx_sql := IF(
    @idx_exists = 0,
    'CREATE INDEX ix_semantic_governance_run_status_lease ON semantic_governance_runs (status, lease_expires_at)',
    'SELECT 1'
);
PREPARE stmt FROM @idx_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE semantic_governance_policies
SET
    run_after_scan = FALSE,
    sample_row_limit = LEAST(sample_row_limit, 1000),
    table_timeout_sec = LEAST(table_timeout_sec, 3),
    max_tables_per_run = LEAST(max_tables_per_run, 5),
    max_run_seconds = LEAST(max_run_seconds, 90)
WHERE run_after_scan = TRUE
   OR sample_row_limit > 1000
   OR table_timeout_sec > 3
   OR max_tables_per_run > 5
   OR max_run_seconds > 90;
