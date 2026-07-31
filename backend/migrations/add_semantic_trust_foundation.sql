-- Trusted semantic governance foundation. Idempotent on MySQL 8.

DROP PROCEDURE IF EXISTS add_semantic_column_if_missing;
DELIMITER //
CREATE PROCEDURE add_semantic_column_if_missing(
    IN p_table VARCHAR(64),
    IN p_column VARCHAR(64),
    IN p_definition TEXT
)
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = p_table AND COLUMN_NAME = p_column
    ) THEN
        SET @ddl = CONCAT('ALTER TABLE `', p_table, '` ADD COLUMN `', p_column, '` ', p_definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END//
DELIMITER ;

CALL add_semantic_column_if_missing('semantic_datasources', 'runtime_mode', 'VARCHAR(20) NOT NULL DEFAULT ''disabled''');
CALL add_semantic_column_if_missing('semantic_datasources', 'schema_fingerprint', 'VARCHAR(64) NULL');
CALL add_semantic_column_if_missing('semantic_datasources', 'last_applied_scan_id', 'INT NULL');
CALL add_semantic_column_if_missing('semantic_datasources', 'last_scan_at', 'DATETIME NULL');

CALL add_semantic_column_if_missing('semantic_tables', 'physical_comment', 'TEXT NULL');
CALL add_semantic_column_if_missing('semantic_tables', 'sync_state', 'VARCHAR(20) NOT NULL DEFAULT ''current''');
CALL add_semantic_column_if_missing('semantic_tables', 'origin_source', 'VARCHAR(32) NOT NULL DEFAULT ''legacy_unknown''');
CALL add_semantic_column_if_missing('semantic_tables', 'management_mode', 'VARCHAR(20) NOT NULL DEFAULT ''human''');
CALL add_semantic_column_if_missing('semantic_tables', 'confidence', 'DOUBLE NULL');
CALL add_semantic_column_if_missing('semantic_tables', 'evidence_json', 'JSON NULL');
CALL add_semantic_column_if_missing('semantic_tables', 'stale_reason_json', 'JSON NULL');
CALL add_semantic_column_if_missing('semantic_tables', 'schema_fingerprint', 'VARCHAR(64) NULL');
CALL add_semantic_column_if_missing('semantic_tables', 'last_seen_scan_id', 'INT NULL');
CALL add_semantic_column_if_missing('semantic_tables', 'confirmed_by', 'VARCHAR(64) NULL');
CALL add_semantic_column_if_missing('semantic_tables', 'confirmed_at', 'DATETIME NULL');

CALL add_semantic_column_if_missing('semantic_columns', 'physical_comment', 'TEXT NULL');
CALL add_semantic_column_if_missing('semantic_columns', 'sync_state', 'VARCHAR(20) NOT NULL DEFAULT ''current''');
CALL add_semantic_column_if_missing('semantic_columns', 'origin_source', 'VARCHAR(32) NOT NULL DEFAULT ''legacy_unknown''');
CALL add_semantic_column_if_missing('semantic_columns', 'management_mode', 'VARCHAR(20) NOT NULL DEFAULT ''human''');
CALL add_semantic_column_if_missing('semantic_columns', 'confidence', 'DOUBLE NULL');
CALL add_semantic_column_if_missing('semantic_columns', 'evidence_json', 'JSON NULL');
CALL add_semantic_column_if_missing('semantic_columns', 'stale_reason_json', 'JSON NULL');
CALL add_semantic_column_if_missing('semantic_columns', 'schema_fingerprint', 'VARCHAR(64) NULL');
CALL add_semantic_column_if_missing('semantic_columns', 'last_seen_scan_id', 'INT NULL');
CALL add_semantic_column_if_missing('semantic_columns', 'confirmed_by', 'VARCHAR(64) NULL');
CALL add_semantic_column_if_missing('semantic_columns', 'confirmed_at', 'DATETIME NULL');

CALL add_semantic_column_if_missing('semantic_metrics', 'sync_state', 'VARCHAR(20) NOT NULL DEFAULT ''current''');
CALL add_semantic_column_if_missing('semantic_metrics', 'origin_source', 'VARCHAR(32) NOT NULL DEFAULT ''legacy_unknown''');
CALL add_semantic_column_if_missing('semantic_metrics', 'management_mode', 'VARCHAR(20) NOT NULL DEFAULT ''human''');
CALL add_semantic_column_if_missing('semantic_metrics', 'confidence', 'DOUBLE NULL');
CALL add_semantic_column_if_missing('semantic_metrics', 'evidence_json', 'JSON NULL');
CALL add_semantic_column_if_missing('semantic_metrics', 'stale_reason_json', 'JSON NULL');
CALL add_semantic_column_if_missing('semantic_metrics', 'schema_fingerprint', 'VARCHAR(64) NULL');
CALL add_semantic_column_if_missing('semantic_metrics', 'last_seen_scan_id', 'INT NULL');
CALL add_semantic_column_if_missing('semantic_metrics', 'confirmed_by', 'VARCHAR(64) NULL');
CALL add_semantic_column_if_missing('semantic_metrics', 'confirmed_at', 'DATETIME NULL');

CALL add_semantic_column_if_missing('semantic_relationships', 'sync_state', 'VARCHAR(20) NOT NULL DEFAULT ''current''');
CALL add_semantic_column_if_missing('semantic_relationships', 'origin_source', 'VARCHAR(32) NOT NULL DEFAULT ''legacy_unknown''');
CALL add_semantic_column_if_missing('semantic_relationships', 'management_mode', 'VARCHAR(20) NOT NULL DEFAULT ''human''');
CALL add_semantic_column_if_missing('semantic_relationships', 'evidence_json', 'JSON NULL');
CALL add_semantic_column_if_missing('semantic_relationships', 'stale_reason_json', 'JSON NULL');
CALL add_semantic_column_if_missing('semantic_relationships', 'schema_fingerprint', 'VARCHAR(64) NULL');
CALL add_semantic_column_if_missing('semantic_relationships', 'last_seen_scan_id', 'INT NULL');
CALL add_semantic_column_if_missing('semantic_relationships', 'confirmed_by', 'VARCHAR(64) NULL');
CALL add_semantic_column_if_missing('semantic_relationships', 'confirmed_at', 'DATETIME NULL');

CALL add_semantic_column_if_missing('semantic_query_runs', 'runtime_mode', 'VARCHAR(20) NOT NULL DEFAULT ''disabled''');
CALL add_semantic_column_if_missing('semantic_query_runs', 'correlation_id', 'VARCHAR(64) NULL');
CALL add_semantic_column_if_missing('semantic_query_runs', 'semantic_result_json', 'JSON NULL');
CALL add_semantic_column_if_missing('semantic_query_runs', 'legacy_result_json', 'JSON NULL');
CALL add_semantic_column_if_missing('semantic_query_runs', 'comparison_json', 'JSON NULL');
CALL add_semantic_column_if_missing('semantic_query_runs', 'returned_chain', 'VARCHAR(20) NULL');

DROP PROCEDURE add_semantic_column_if_missing;

CREATE TABLE IF NOT EXISTS semantic_scan_runs (
    id INT PRIMARY KEY AUTO_INCREMENT,
    workspace_id VARCHAR(64) NOT NULL,
    datasource_id INT NOT NULL,
    created_by VARCHAR(64) NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'previewed',
    base_fingerprint VARCHAR(64) NULL,
    target_fingerprint VARCHAR(64) NOT NULL,
    snapshot_json JSON NOT NULL,
    diff_json JSON NOT NULL,
    impact_json JSON NOT NULL,
    summary_json JSON NOT NULL,
    error_message TEXT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    applied_at DATETIME NULL,
    INDEX idx_semantic_scan_workspace (workspace_id),
    INDEX idx_semantic_scan_datasource (datasource_id),
    INDEX idx_semantic_scan_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS semantic_governance_events (
    id INT PRIMARY KEY AUTO_INCREMENT,
    workspace_id VARCHAR(64) NOT NULL,
    datasource_id INT NULL,
    actor_id VARCHAR(64) NULL,
    action VARCHAR(64) NOT NULL,
    object_type VARCHAR(32) NULL,
    object_id INT NULL,
    scan_id INT NULL,
    payload_json JSON NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_semantic_event_workspace (workspace_id),
    INDEX idx_semantic_event_action (action),
    INDEX idx_semantic_event_scan (scan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

UPDATE semantic_datasources
SET runtime_mode = CASE
    WHEN semantic_sql_enabled = 0 THEN 'disabled'
    WHEN semantic_sql_fallback_enabled = 1 THEN 'shadow'
    ELSE 'trusted'
END;

UPDATE semantic_tables SET
    origin_source = CASE WHEN status = 'confirmed' THEN 'legacy_human' ELSE 'legacy_unknown' END,
    management_mode = 'human', sync_state = 'current',
    confidence = CASE WHEN status = 'confirmed' THEN 1.0 ELSE confidence END,
    evidence_json = COALESCE(evidence_json, JSON_OBJECT()),
    stale_reason_json = COALESCE(stale_reason_json, JSON_OBJECT());
UPDATE semantic_columns SET
    origin_source = CASE WHEN status = 'confirmed' THEN 'legacy_human' ELSE 'legacy_unknown' END,
    management_mode = 'human', sync_state = 'current',
    confidence = CASE WHEN status = 'confirmed' THEN 1.0 ELSE confidence END,
    evidence_json = COALESCE(evidence_json, JSON_OBJECT()),
    stale_reason_json = COALESCE(stale_reason_json, JSON_OBJECT());
UPDATE semantic_metrics SET
    origin_source = CASE WHEN status = 'confirmed' THEN 'legacy_human' ELSE 'legacy_unknown' END,
    management_mode = 'human', sync_state = 'current',
    confidence = CASE WHEN status = 'confirmed' THEN 1.0 ELSE confidence END,
    evidence_json = COALESCE(evidence_json, JSON_OBJECT()),
    stale_reason_json = COALESCE(stale_reason_json, JSON_OBJECT());
UPDATE semantic_relationships SET
    origin_source = CASE WHEN status = 'confirmed' THEN 'legacy_human' ELSE 'legacy_unknown' END,
    management_mode = 'human', sync_state = 'current',
    evidence_json = COALESCE(evidence_json, JSON_OBJECT()),
    stale_reason_json = COALESCE(stale_reason_json, JSON_OBJECT());
