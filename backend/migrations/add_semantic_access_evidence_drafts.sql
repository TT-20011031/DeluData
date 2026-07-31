-- Link editable first-permission drafts to their source evidence set.

SET @evidence_set_id_exists := (
    SELECT COUNT(1) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'semantic_access_bootstrap_runs'
      AND COLUMN_NAME = 'evidence_set_id'
);
SET @evidence_set_id_sql := IF(
    @evidence_set_id_exists = 0,
    'ALTER TABLE semantic_access_bootstrap_runs ADD COLUMN evidence_set_id INT NULL AFTER datasource_id',
    'SELECT 1'
);
PREPARE stmt FROM @evidence_set_id_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @evidence_set_id_index_exists := (
    SELECT COUNT(1) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'semantic_access_bootstrap_runs'
      AND INDEX_NAME = 'ix_semantic_access_bootstrap_evidence_set_id'
);
SET @evidence_set_id_index_sql := IF(
    @evidence_set_id_index_exists = 0,
    'CREATE INDEX ix_semantic_access_bootstrap_evidence_set_id ON semantic_access_bootstrap_runs (evidence_set_id)',
    'SELECT 1'
);
PREPARE stmt FROM @evidence_set_id_index_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
