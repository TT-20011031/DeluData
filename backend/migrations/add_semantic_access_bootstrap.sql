-- AI-assisted first-time semantic access configuration. MySQL 8.0+.

CREATE TABLE IF NOT EXISTS semantic_access_bootstrap_runs (
    id INT NOT NULL AUTO_INCREMENT,
    workspace_id VARCHAR(64) NOT NULL,
    datasource_id INT NOT NULL,
    evidence_set_id INT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    stage VARCHAR(32) NOT NULL DEFAULT 'queued',
    progress INT NOT NULL DEFAULT 0,
    revision INT NOT NULL DEFAULT 0,
    schema_fingerprint VARCHAR(64) NULL,
    organization_fingerprint VARCHAR(64) NOT NULL,
    business_context TEXT NULL,
    input_snapshot_json JSON NOT NULL,
    summary_json JSON NOT NULL,
    error_message TEXT NULL,
    triggered_by VARCHAR(64) NULL,
    applied_by VARCHAR(64) NULL,
    worker_id VARCHAR(128) NULL,
    run_token VARCHAR(64) NULL,
    lease_expires_at DATETIME NULL,
    heartbeat_at DATETIME NULL,
    attempt INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 2,
    cancel_requested_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    started_at DATETIME NULL,
    completed_at DATETIME NULL,
    applied_at DATETIME NULL,
    PRIMARY KEY (id),
    KEY ix_semantic_access_bootstrap_workspace (workspace_id, datasource_id, created_at),
    KEY ix_semantic_access_bootstrap_evidence_set_id (evidence_set_id),
    KEY ix_semantic_access_bootstrap_run_claim (status, lease_expires_at, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS semantic_access_bootstrap_targets (
    id INT NOT NULL AUTO_INCREMENT,
    workspace_id VARCHAR(64) NOT NULL,
    datasource_id INT NOT NULL,
    run_id INT NOT NULL,
    target_type VARCHAR(24) NOT NULL,
    target_id VARCHAR(64) NOT NULL,
    target_label VARCHAR(128) NOT NULL,
    include_descendants TINYINT(1) NOT NULL DEFAULT 0,
    base_binding_id INT NULL,
    base_revision INT NOT NULL DEFAULT 0,
    included TINYINT(1) NOT NULL DEFAULT 1,
    status VARCHAR(24) NOT NULL DEFAULT 'proposed',
    confidence DOUBLE NOT NULL DEFAULT 0,
    definition_json JSON NOT NULL,
    candidates_json JSON NOT NULL,
    explanation_json JSON NOT NULL,
    validation_json JSON NOT NULL,
    edited_by VARCHAR(64) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_semantic_access_bootstrap_target (run_id, target_type, target_id),
    KEY ix_semantic_access_bootstrap_target_workspace (workspace_id, datasource_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS semantic_access_bootstrap_mappings (
    id INT NOT NULL AUTO_INCREMENT,
    workspace_id VARCHAR(64) NOT NULL,
    datasource_id INT NOT NULL,
    run_id INT NOT NULL,
    table_id INT NOT NULL,
    table_label VARCHAR(128) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'proposed',
    accepted TINYINT(1) NOT NULL DEFAULT 0,
    confidence DOUBLE NOT NULL DEFAULT 0,
    base_mapping_json JSON NOT NULL,
    proposed_mapping_json JSON NOT NULL,
    evidence_json JSON NOT NULL,
    validation_json JSON NOT NULL,
    edited_by VARCHAR(64) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_semantic_access_bootstrap_mapping (run_id, table_id),
    KEY ix_semantic_access_bootstrap_mapping_workspace (workspace_id, datasource_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET @source_type_exists := (
    SELECT COUNT(1) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'semantic_policy_versions' AND COLUMN_NAME = 'source_type'
);
SET @source_type_sql := IF(
    @source_type_exists = 0,
    'ALTER TABLE semantic_policy_versions ADD COLUMN source_type VARCHAR(32) NOT NULL DEFAULT ''manual'' AFTER source_text',
    'SELECT 1'
);
PREPARE stmt FROM @source_type_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @source_ref_exists := (
    SELECT COUNT(1) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'semantic_policy_versions' AND COLUMN_NAME = 'source_ref'
);
SET @source_ref_sql := IF(
    @source_ref_exists = 0,
    'ALTER TABLE semantic_policy_versions ADD COLUMN source_ref VARCHAR(128) NULL AFTER source_type',
    'SELECT 1'
);
PREPARE stmt FROM @source_ref_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @source_ref_index_exists := (
    SELECT COUNT(1) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'semantic_policy_versions' AND INDEX_NAME = 'ix_semantic_policy_versions_source_ref'
);
SET @source_ref_index_sql := IF(
    @source_ref_index_exists = 0,
    'CREATE INDEX ix_semantic_policy_versions_source_ref ON semantic_policy_versions (source_ref)',
    'SELECT 1'
);
PREPARE stmt FROM @source_ref_index_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
