-- Indexes for trusted-governance scan history/readiness queries.
-- Idempotent on MySQL 8.

DROP PROCEDURE IF EXISTS add_semantic_index_if_missing;
DELIMITER //
CREATE PROCEDURE add_semantic_index_if_missing(
    IN p_table VARCHAR(64),
    IN p_index VARCHAR(64),
    IN p_definition TEXT
)
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = p_table
          AND INDEX_NAME = p_index
    ) THEN
        SET @ddl = CONCAT('ALTER TABLE `', p_table, '` ADD INDEX `', p_index, '` ', p_definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END//
DELIMITER ;

CALL add_semantic_index_if_missing(
    'semantic_scan_runs',
    'idx_semantic_scan_lookup_created',
    '(workspace_id, datasource_id, status, created_at)'
);

CALL add_semantic_index_if_missing(
    'semantic_scan_runs',
    'idx_semantic_scan_workspace_created',
    '(workspace_id, created_at)'
);

DROP PROCEDURE add_semantic_index_if_missing;
