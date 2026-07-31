SET @column_exists := (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'workspace_knowledge_governance'
      AND COLUMN_NAME = 'max_upload_file_size_bytes'
);

SET @sql := IF(
    @column_exists = 0,
    'ALTER TABLE workspace_knowledge_governance ADD COLUMN max_upload_file_size_bytes BIGINT NULL AFTER storage_quota_bytes',
    'SELECT 1'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
