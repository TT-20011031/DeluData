-- Add semantic table row-level scope configuration.
-- Kept idempotent without relying on ALTER TABLE ADD COLUMN IF NOT EXISTS.

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

CALL add_semantic_table_column_if_missing('row_scope_enabled', 'TINYINT(1) NOT NULL DEFAULT 1');
CALL add_semantic_table_column_if_missing('row_scope_mode', 'VARCHAR(20) NOT NULL DEFAULT ''required''');
CALL add_semantic_table_column_if_missing('row_scope_user_column_id', 'INT NULL');
CALL add_semantic_table_column_if_missing('row_scope_dept_column_id', 'INT NULL');

DROP PROCEDURE IF EXISTS add_semantic_table_column_if_missing;
