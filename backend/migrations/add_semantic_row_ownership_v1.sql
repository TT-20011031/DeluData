-- AI-assisted row ownership mappings. MySQL 8.0+.

SET @org_value_mapping_exists := (
    SELECT COUNT(1) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'semantic_ownership_mappings'
      AND COLUMN_NAME = 'org_value_mapping_json'
);
SET @org_value_mapping_sql := IF(
    @org_value_mapping_exists = 0,
    'ALTER TABLE semantic_ownership_mappings ADD COLUMN org_value_mapping_json JSON NULL AFTER org_value_kind',
    'SELECT 1'
);
PREPARE stmt FROM @org_value_mapping_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE semantic_ownership_mappings
SET org_value_mapping_json = JSON_OBJECT()
WHERE org_value_mapping_json IS NULL;

SET @org_value_mapping_nullable := (
    SELECT IS_NULLABLE FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'semantic_ownership_mappings'
      AND COLUMN_NAME = 'org_value_mapping_json'
);
SET @org_value_mapping_not_null_sql := IF(
    @org_value_mapping_nullable = 'YES',
    'ALTER TABLE semantic_ownership_mappings MODIFY COLUMN org_value_mapping_json JSON NOT NULL',
    'SELECT 1'
);
PREPARE stmt FROM @org_value_mapping_not_null_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
