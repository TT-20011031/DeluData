-- Add system-managed semantic row permission rules.

DROP PROCEDURE IF EXISTS add_semantic_table_column_if_missing;
DELIMITER //
CREATE PROCEDURE add_semantic_table_column_if_missing(
    IN p_column VARCHAR(64),
    IN p_definition TEXT
)
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'semantic_tables'
          AND COLUMN_NAME = p_column
    ) THEN
        SET @ddl = CONCAT('ALTER TABLE `semantic_tables` ADD COLUMN `', p_column, '` ', p_definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END//
DELIMITER ;

CALL add_semantic_table_column_if_missing('row_permission_mode', 'VARCHAR(20) NOT NULL DEFAULT ''off''');

DROP PROCEDURE IF EXISTS add_semantic_table_column_if_missing;

CREATE TABLE IF NOT EXISTS semantic_row_permissions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    workspace_id VARCHAR(64) NOT NULL,
    datasource_id INT NOT NULL,
    table_id INT NOT NULL,
    subject_type VARCHAR(16) NOT NULL,
    subject_id VARCHAR(64) NOT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    condition_json JSON NOT NULL,
    created_by VARCHAR(64) NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX ix_semantic_row_perm_workspace_table (workspace_id, datasource_id, table_id),
    INDEX ix_semantic_row_perm_subject (workspace_id, subject_type, subject_id)
);
