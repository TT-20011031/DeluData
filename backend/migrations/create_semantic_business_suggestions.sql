-- Business semantic suggestions for table/column governance.

CREATE TABLE IF NOT EXISTS semantic_business_suggestions (
    id INT PRIMARY KEY AUTO_INCREMENT,
    workspace_id VARCHAR(64) NOT NULL,
    datasource_id INT NOT NULL,
    object_type VARCHAR(20) NOT NULL,
    object_id INT NOT NULL,
    physical_name VARCHAR(128) NOT NULL,
    suggested_business_name VARCHAR(128) NOT NULL,
    suggested_description TEXT NULL,
    suggested_synonyms JSON NOT NULL,
    confidence DOUBLE NOT NULL DEFAULT 0.5,
    source VARCHAR(32) NOT NULL DEFAULT 'rule',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    evidence_json JSON NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    accepted_at DATETIME NULL,
    INDEX idx_semantic_business_suggestions_workspace (workspace_id),
    INDEX idx_semantic_business_suggestions_datasource (datasource_id),
    INDEX idx_semantic_business_suggestions_object (object_type, object_id),
    INDEX idx_semantic_business_suggestions_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
