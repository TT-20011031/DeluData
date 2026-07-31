-- ERP-style organization + role authorization and versioned semantic policies.
-- MySQL 8.0+. This migration is additive so the previous model remains rollback-safe.

CREATE TABLE IF NOT EXISTS sys_schema_migrations (
  migration_id VARCHAR(128) NOT NULL, checksum VARCHAR(64) NOT NULL,
  status VARCHAR(24) NOT NULL, report_json JSON NOT NULL,
  applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (migration_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS authorization_migration_review_items (
  id INT NOT NULL AUTO_INCREMENT, workspace_id VARCHAR(64) NOT NULL,
  item_type VARCHAR(64) NOT NULL, source_id VARCHAR(64) NOT NULL,
  reason TEXT NOT NULL, status VARCHAR(24) NOT NULL DEFAULT 'pending',
  payload_json JSON NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id), UNIQUE KEY uq_auth_migration_review_source (item_type, source_id),
  KEY ix_auth_migration_review_workspace (workspace_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS authorization_migration_source_map (
  id INT NOT NULL AUTO_INCREMENT, workspace_id VARCHAR(64) NOT NULL,
  source_type VARCHAR(64) NOT NULL, source_id VARCHAR(64) NOT NULL,
  target_type VARCHAR(64) NOT NULL, target_id VARCHAR(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id), UNIQUE KEY uq_auth_migration_source_target (source_type, source_id, target_type, target_id),
  KEY ix_auth_migration_source (source_type, source_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS authorization_migration_user_backup (
  user_id VARCHAR(36) NOT NULL, workspace_id VARCHAR(36) NOT NULL,
  original_department_id INT NULL, captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS authorization_migration_role_map (
  global_role_id INT NOT NULL, workspace_id VARCHAR(36) NOT NULL, local_role_id INT NOT NULL,
  PRIMARY KEY (global_role_id, workspace_id), UNIQUE KEY uq_auth_migration_local_role (local_role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS authorization_migration_wildcard_roles (
  role_id INT NOT NULL, PRIMARY KEY (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sys_user_table_permissions_archive (
  source_id INT NOT NULL, user_id VARCHAR(36) NOT NULL, table_name VARCHAR(128) NOT NULL,
  archived_reason VARCHAR(255) NOT NULL, archived_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (source_id), KEY ix_user_table_permission_archive_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sys_positions (
  id INT NOT NULL AUTO_INCREMENT, workspace_id VARCHAR(36) NOT NULL,
  org_unit_id INT NOT NULL, name VARCHAR(96) NOT NULL, code VARCHAR(64) NULL,
  status TINYINT(1) NOT NULL DEFAULT 1, legacy_generated TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id), UNIQUE KEY uq_position_org_name (workspace_id, org_unit_id, name),
  KEY ix_position_workspace_status (workspace_id, status), KEY ix_position_org (org_unit_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sys_assignments (
  id INT NOT NULL AUTO_INCREMENT, workspace_id VARCHAR(36) NOT NULL,
  user_id VARCHAR(36) NOT NULL, position_id INT NOT NULL, is_primary TINYINT(1) NOT NULL DEFAULT 0,
  starts_at DATETIME NOT NULL, ends_at DATETIME NULL, status TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id), KEY ix_assignment_effective (workspace_id, user_id, status, starts_at, ends_at),
  KEY ix_assignment_position (position_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sys_role_bindings (
  id INT NOT NULL AUTO_INCREMENT, workspace_id VARCHAR(36) NOT NULL,
  position_id INT NOT NULL, role_id INT NOT NULL, scope_type VARCHAR(24) NOT NULL DEFAULT 'self',
  scope_org_unit_id INT NULL, custom_org_unit_ids JSON NOT NULL,
  starts_at DATETIME NOT NULL, ends_at DATETIME NULL, status TINYINT(1) NOT NULL DEFAULT 1,
  created_by VARCHAR(36) NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id), KEY ix_role_binding_effective (workspace_id, status, starts_at, ends_at),
  KEY ix_role_binding_position (position_id), KEY ix_role_binding_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sys_authorization_exceptions (
  id INT NOT NULL AUTO_INCREMENT, workspace_id VARCHAR(36) NOT NULL, user_id VARCHAR(36) NOT NULL,
  effect_type VARCHAR(8) NOT NULL, capability_codes JSON NOT NULL, scope_type VARCHAR(24) NOT NULL,
  scope_org_unit_ids JSON NOT NULL, reason TEXT NOT NULL, owner_id VARCHAR(36) NOT NULL,
  starts_at DATETIME NOT NULL, ends_at DATETIME NOT NULL, status TINYINT(1) NOT NULL DEFAULT 1,
  created_by VARCHAR(36) NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id), KEY ix_auth_exception_effective (workspace_id, user_id, status, starts_at, ends_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sys_authorization_audit_events (
  id INT NOT NULL AUTO_INCREMENT, workspace_id VARCHAR(36) NOT NULL, actor_id VARCHAR(36) NOT NULL,
  action VARCHAR(64) NOT NULL, target_type VARCHAR(64) NOT NULL, target_id VARCHAR(64) NULL,
  before_json JSON NOT NULL, after_json JSON NOT NULL, reason TEXT NULL, request_id VARCHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id), KEY ix_auth_audit_workspace_created (workspace_id, created_at),
  KEY ix_auth_audit_actor (actor_id), KEY ix_auth_audit_action (action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS sys_authorization_revisions (
  workspace_id VARCHAR(36) NOT NULL, revision INT NOT NULL DEFAULT 1,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS semantic_policy_bindings (
  id INT NOT NULL AUTO_INCREMENT, workspace_id VARCHAR(64) NOT NULL, datasource_id INT NOT NULL,
  target_type VARCHAR(24) NOT NULL, target_id VARCHAR(64) NOT NULL DEFAULT '*',
  active_version_id INT NULL, revision INT NOT NULL DEFAULT 0, status TINYINT(1) NOT NULL DEFAULT 1,
  created_by VARCHAR(64) NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id), UNIQUE KEY uq_semantic_policy_binding_target (workspace_id, datasource_id, target_type, target_id),
  KEY ix_semantic_policy_binding_runtime (workspace_id, datasource_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS semantic_policy_versions (
  id INT NOT NULL AUTO_INCREMENT, workspace_id VARCHAR(64) NOT NULL, datasource_id INT NOT NULL,
  binding_id INT NOT NULL, version INT NOT NULL, definition_json JSON NOT NULL, source_text TEXT NULL,
  validation_json JSON NOT NULL, compile_summary_json JSON NOT NULL, schema_fingerprint VARCHAR(64) NULL,
  created_by VARCHAR(64) NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id), UNIQUE KEY uq_semantic_policy_binding_version (binding_id, version),
  KEY ix_semantic_policy_version_workspace (workspace_id, datasource_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS semantic_policy_version_effects (
  id INT NOT NULL AUTO_INCREMENT, workspace_id VARCHAR(64) NOT NULL, datasource_id INT NOT NULL,
  binding_id INT NOT NULL, version_id INT NOT NULL, effect_key VARCHAR(128) NOT NULL,
  asset_type VARCHAR(16) NOT NULL, asset_id INT NOT NULL, effect_type VARCHAR(32) NOT NULL,
  condition_json JSON NOT NULL, compiled_sql TEXT NULL, priority INT NOT NULL DEFAULT 100,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id), UNIQUE KEY uq_semantic_policy_version_effect (version_id, effect_key),
  KEY ix_semantic_policy_version_effect_asset (workspace_id, datasource_id, asset_type, asset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS semantic_ownership_mappings (
  id INT NOT NULL AUTO_INCREMENT, workspace_id VARCHAR(64) NOT NULL, datasource_id INT NOT NULL,
  table_id INT NOT NULL, org_column_id INT NULL, org_value_kind VARCHAR(16) NOT NULL DEFAULT 'id',
  user_column_id INT NULL, user_value_kind VARCHAR(16) NOT NULL DEFAULT 'id', updated_by VARCHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id), UNIQUE KEY uq_semantic_ownership_mapping (workspace_id, datasource_id, table_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO sys_authorization_revisions (workspace_id, revision, updated_at)
SELECT DISTINCT workspace_id, 1, CURRENT_TIMESTAMP FROM sys_users
ON DUPLICATE KEY UPDATE revision = revision;
