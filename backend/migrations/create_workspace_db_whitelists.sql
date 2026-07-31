-- Workspace DB whitelist table
CREATE TABLE IF NOT EXISTS workspace_db_whitelists (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  workspace_id VARCHAR(64) NOT NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 0,
  allowed_endpoints JSON NOT NULL,
  note TEXT NULL,
  updated_by VARCHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_workspace_db_whitelists_workspace_id (workspace_id),
  KEY ix_workspace_db_whitelists_workspace_id (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
