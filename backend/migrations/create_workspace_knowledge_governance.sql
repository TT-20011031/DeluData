-- Workspace knowledge governance table
CREATE TABLE IF NOT EXISTS workspace_knowledge_governance (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  workspace_id VARCHAR(64) NOT NULL,
  storage_quota_bytes BIGINT NULL,
  max_upload_file_size_bytes BIGINT NULL,
  upload_enabled TINYINT(1) NOT NULL DEFAULT 1,
  delete_enabled TINYINT(1) NOT NULL DEFAULT 1,
  rename_enabled TINYINT(1) NOT NULL DEFAULT 1,
  move_enabled TINYINT(1) NOT NULL DEFAULT 1,
  create_folder_enabled TINYINT(1) NOT NULL DEFAULT 1,
  note TEXT NULL,
  updated_by VARCHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_workspace_knowledge_governance_workspace_id (workspace_id),
  KEY ix_workspace_knowledge_governance_workspace_id (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
