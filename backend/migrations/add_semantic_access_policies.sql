-- Unified semantic access policies. MySQL 8.0+.

CREATE TABLE IF NOT EXISTS semantic_access_policies (
    id INT NOT NULL AUTO_INCREMENT,
    workspace_id VARCHAR(64) NOT NULL,
    datasource_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT NULL,
    source_type VARCHAR(32) NOT NULL DEFAULT 'manual',
    source_text TEXT NULL,
    source_ref VARCHAR(255) NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    subject_json JSON NOT NULL,
    asset_selector_json JSON NOT NULL,
    constraint_json JSON NOT NULL,
    compile_summary_json JSON NOT NULL,
    validation_json JSON NOT NULL,
    model_version INT NOT NULL DEFAULT 1,
    policy_version INT NOT NULL DEFAULT 1,
    schema_fingerprint VARCHAR(64) NULL,
    created_by VARCHAR(64) NULL,
    activated_by VARCHAR(64) NULL,
    activated_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_semantic_access_policy_source (workspace_id, datasource_id, source_ref),
    KEY ix_semantic_access_policy_runtime (workspace_id, datasource_id, status),
    KEY ix_semantic_access_policy_model_version (model_version),
    KEY ix_semantic_access_policy_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS semantic_access_policy_effects (
    id INT NOT NULL AUTO_INCREMENT,
    workspace_id VARCHAR(64) NOT NULL,
    datasource_id INT NOT NULL,
    policy_id INT NOT NULL,
    effect_key VARCHAR(128) NOT NULL,
    subject_type VARCHAR(16) NOT NULL,
    subject_id VARCHAR(64) NOT NULL DEFAULT '*',
    asset_type VARCHAR(16) NOT NULL,
    asset_id INT NOT NULL,
    effect_type VARCHAR(32) NOT NULL,
    condition_json JSON NOT NULL,
    compiled_sql TEXT NULL,
    priority INT NOT NULL DEFAULT 100,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    policy_version INT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_semantic_access_policy_effect (policy_id, effect_key),
    KEY ix_semantic_access_effect_runtime (workspace_id, datasource_id, subject_type, subject_id, enabled),
    KEY ix_semantic_access_effect_asset (workspace_id, datasource_id, asset_type, asset_id, effect_type),
    KEY ix_semantic_access_effect_policy (policy_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS semantic_asset_tags (
    id INT NOT NULL AUTO_INCREMENT,
    workspace_id VARCHAR(64) NOT NULL,
    datasource_id INT NOT NULL,
    asset_type VARCHAR(16) NOT NULL,
    asset_id INT NOT NULL,
    tag_type VARCHAR(32) NOT NULL,
    tag_value VARCHAR(128) NOT NULL,
    source VARCHAR(32) NOT NULL DEFAULT 'manual',
    confidence DOUBLE NULL,
    created_by VARCHAR(64) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_semantic_asset_tag (
        workspace_id, datasource_id, asset_type, asset_id, tag_type, tag_value
    ),
    KEY ix_semantic_asset_tag_lookup (
        workspace_id, datasource_id, asset_type, tag_type, tag_value
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Keep a fail-closed marker for every table that previously required row rules.
INSERT IGNORE INTO semantic_access_policies (
    workspace_id, datasource_id, name, description, source_type, source_text,
    source_ref, status, subject_json, asset_selector_json, constraint_json,
    compile_summary_json, validation_json, policy_version, schema_fingerprint,
    created_by, activated_by, activated_at
)
SELECT
    t.workspace_id,
    t.datasource_id,
    CONCAT('迁移行级保护：', t.business_name),
    '由旧 row_permission_mode 自动迁移；没有匹配过滤规则时拒绝访问。',
    'legacy_import',
    NULL,
    CONCAT('legacy:row-required:', t.id),
    'active',
    JSON_ARRAY(),
    JSON_OBJECT('table_ids', JSON_ARRAY(t.id)),
    JSON_ARRAY(JSON_OBJECT('effect', 'row_filter', 'table_id', t.id)),
    JSON_OBJECT('migrated', TRUE, 'fail_closed', TRUE),
    JSON_OBJECT('blockers', JSON_ARRAY(), 'warnings', JSON_ARRAY()),
    1,
    t.schema_fingerprint,
    NULL,
    NULL,
    CURRENT_TIMESTAMP
FROM semantic_tables t
WHERE t.row_permission_mode = 'required';

INSERT IGNORE INTO semantic_access_policy_effects (
    workspace_id, datasource_id, policy_id, effect_key, subject_type, subject_id,
    asset_type, asset_id, effect_type, condition_json, priority, enabled, policy_version
)
SELECT
    p.workspace_id, p.datasource_id, p.id, 'row-restricted', 'scope', '*',
    'table', t.id, 'row_restricted', JSON_OBJECT(), 100, 1, p.policy_version
FROM semantic_access_policies p
JOIN semantic_tables t
  ON p.workspace_id COLLATE utf8mb4_unicode_ci = t.workspace_id COLLATE utf8mb4_unicode_ci
 AND p.datasource_id = t.datasource_id
 AND p.source_ref COLLATE utf8mb4_unicode_ci = CONVERT(CONCAT('legacy:row-required:', t.id) USING utf8mb4) COLLATE utf8mb4_unicode_ci;

-- Convert each enabled legacy row rule into an active policy and deterministic effect.
INSERT IGNORE INTO semantic_access_policies (
    workspace_id, datasource_id, name, description, source_type, source_ref, status,
    subject_json, asset_selector_json, constraint_json, compile_summary_json,
    validation_json, policy_version, schema_fingerprint, created_by, activated_by, activated_at
)
SELECT
    r.workspace_id,
    r.datasource_id,
    CONCAT('迁移行级规则 #', r.id),
    '由旧 semantic_row_permissions 自动迁移。',
    'legacy_import',
    CONCAT('legacy:row-rule:', r.id),
    'active',
    JSON_ARRAY(JSON_OBJECT('type', r.subject_type, 'id', r.subject_id)),
    JSON_OBJECT('table_ids', JSON_ARRAY(r.table_id)),
    JSON_ARRAY(JSON_OBJECT('effect', 'row_filter', 'table_id', r.table_id, 'condition', r.condition_json)),
    JSON_OBJECT('migrated', TRUE, 'effect_count', 1),
    JSON_OBJECT('blockers', JSON_ARRAY(), 'warnings', JSON_ARRAY()),
    1,
    t.schema_fingerprint,
    r.created_by,
    r.created_by,
    CURRENT_TIMESTAMP
FROM semantic_row_permissions r
JOIN semantic_tables t ON t.id = r.table_id AND t.workspace_id = r.workspace_id
WHERE r.enabled = 1 AND t.row_permission_mode = 'required';

INSERT IGNORE INTO semantic_access_policy_effects (
    workspace_id, datasource_id, policy_id, effect_key, subject_type, subject_id,
    asset_type, asset_id, effect_type, condition_json, priority, enabled, policy_version
)
SELECT
    p.workspace_id, p.datasource_id, p.id, 'row-filter', r.subject_type, r.subject_id,
    'table', r.table_id, 'row_filter', r.condition_json, 100, 1, p.policy_version
FROM semantic_access_policies p
JOIN semantic_row_permissions r
  ON p.source_ref COLLATE utf8mb4_unicode_ci = CONVERT(CONCAT('legacy:row-rule:', r.id) USING utf8mb4) COLLATE utf8mb4_unicode_ci
 AND p.workspace_id COLLATE utf8mb4_unicode_ci = r.workspace_id COLLATE utf8mb4_unicode_ci
 AND p.datasource_id = r.datasource_id;

-- Table visibility lists become restricted assets plus subject-specific allows.
INSERT IGNORE INTO semantic_access_policies (
    workspace_id, datasource_id, name, description, source_type, source_ref, status,
    subject_json, asset_selector_json, constraint_json, compile_summary_json,
    validation_json, policy_version, schema_fingerprint, activated_at
)
SELECT
    t.workspace_id, t.datasource_id, CONCAT('迁移表权限：', t.business_name),
    '由旧 visible_role_ids 自动迁移。', 'legacy_import',
    CONCAT('legacy:table:', t.id, ':role:', roles.subject_id), 'active',
    JSON_ARRAY(JSON_OBJECT('type', 'role', 'id', roles.subject_id)),
    JSON_OBJECT('table_ids', JSON_ARRAY(t.id)),
    JSON_ARRAY(JSON_OBJECT('effect', 'table_allow', 'asset_type', 'table', 'asset_ids', JSON_ARRAY(t.id))),
    JSON_OBJECT('migrated', TRUE, 'effect_count', 2),
    JSON_OBJECT('blockers', JSON_ARRAY(), 'warnings', JSON_ARRAY()),
    1, t.schema_fingerprint, CURRENT_TIMESTAMP
FROM semantic_tables t
JOIN JSON_TABLE(
    COALESCE(t.visible_role_ids, JSON_ARRAY()), '$[*]'
    COLUMNS(subject_id VARCHAR(64) PATH '$')
) roles;

INSERT IGNORE INTO semantic_access_policies (
    workspace_id, datasource_id, name, description, source_type, source_ref, status,
    subject_json, asset_selector_json, constraint_json, compile_summary_json,
    validation_json, policy_version, schema_fingerprint, activated_at
)
SELECT
    t.workspace_id, t.datasource_id, CONCAT('迁移表权限：', t.business_name),
    '由旧 visible_user_ids 自动迁移。', 'legacy_import',
    CONCAT('legacy:table:', t.id, ':user:', users.subject_id), 'active',
    JSON_ARRAY(JSON_OBJECT('type', 'user', 'id', users.subject_id)),
    JSON_OBJECT('table_ids', JSON_ARRAY(t.id)),
    JSON_ARRAY(JSON_OBJECT('effect', 'table_allow', 'asset_type', 'table', 'asset_ids', JSON_ARRAY(t.id))),
    JSON_OBJECT('migrated', TRUE, 'effect_count', 2),
    JSON_OBJECT('blockers', JSON_ARRAY(), 'warnings', JSON_ARRAY()),
    1, t.schema_fingerprint, CURRENT_TIMESTAMP
FROM semantic_tables t
JOIN JSON_TABLE(
    COALESCE(t.visible_user_ids, JSON_ARRAY()), '$[*]'
    COLUMNS(subject_id VARCHAR(64) PATH '$')
) users;

INSERT IGNORE INTO semantic_access_policy_effects (
    workspace_id, datasource_id, policy_id, effect_key, subject_type, subject_id,
    asset_type, asset_id, effect_type, condition_json, priority, enabled, policy_version
)
SELECT p.workspace_id, p.datasource_id, p.id, 'restricted', 'scope', '*',
       'table', t.id, 'table_restricted', JSON_OBJECT(), 100, 1, p.policy_version
FROM semantic_access_policies p
JOIN semantic_tables t
  ON p.workspace_id COLLATE utf8mb4_unicode_ci = t.workspace_id COLLATE utf8mb4_unicode_ci AND p.datasource_id = t.datasource_id
 AND p.source_ref COLLATE utf8mb4_unicode_ci LIKE CONVERT(CONCAT('legacy:table:', t.id, ':%') USING utf8mb4) COLLATE utf8mb4_unicode_ci
UNION ALL
SELECT p.workspace_id, p.datasource_id, p.id, 'allow',
       JSON_UNQUOTE(JSON_EXTRACT(p.subject_json, '$[0].type')),
       JSON_UNQUOTE(JSON_EXTRACT(p.subject_json, '$[0].id')),
       'table', t.id, 'table_allow', JSON_OBJECT(), 100, 1, p.policy_version
FROM semantic_access_policies p
JOIN semantic_tables t
  ON p.workspace_id COLLATE utf8mb4_unicode_ci = t.workspace_id COLLATE utf8mb4_unicode_ci AND p.datasource_id = t.datasource_id
 AND p.source_ref COLLATE utf8mb4_unicode_ci LIKE CONVERT(CONCAT('legacy:table:', t.id, ':%') USING utf8mb4) COLLATE utf8mb4_unicode_ci;

-- Column visibility lists use the same restricted/allow convention.
INSERT IGNORE INTO semantic_access_policies (
    workspace_id, datasource_id, name, description, source_type, source_ref, status,
    subject_json, asset_selector_json, constraint_json, compile_summary_json,
    validation_json, policy_version, schema_fingerprint, activated_at
)
SELECT c.workspace_id, c.datasource_id, CONCAT('迁移字段权限：', c.business_name),
       '由旧字段可见角色自动迁移。', 'legacy_import',
       CONCAT('legacy:column:', c.id, ':role:', subjects.subject_id), 'active',
       JSON_ARRAY(JSON_OBJECT('type', 'role', 'id', subjects.subject_id)),
       JSON_OBJECT('column_ids', JSON_ARRAY(c.id)),
       JSON_ARRAY(JSON_OBJECT('effect', 'column_allow_sensitive', 'asset_type', 'column', 'asset_ids', JSON_ARRAY(c.id))),
       JSON_OBJECT('migrated', TRUE, 'effect_count', 2),
       JSON_OBJECT('blockers', JSON_ARRAY(), 'warnings', JSON_ARRAY()),
       1, c.schema_fingerprint, CURRENT_TIMESTAMP
FROM semantic_columns c
JOIN JSON_TABLE(COALESCE(c.visible_role_ids, JSON_ARRAY()), '$[*]'
    COLUMNS(subject_id VARCHAR(64) PATH '$')) subjects
UNION ALL
SELECT c.workspace_id, c.datasource_id, CONCAT('迁移字段权限：', c.business_name),
       '由旧字段可见用户自动迁移。', 'legacy_import',
       CONCAT('legacy:column:', c.id, ':user:', subjects.subject_id), 'active',
       JSON_ARRAY(JSON_OBJECT('type', 'user', 'id', subjects.subject_id)),
       JSON_OBJECT('column_ids', JSON_ARRAY(c.id)),
       JSON_ARRAY(JSON_OBJECT('effect', 'column_allow_sensitive', 'asset_type', 'column', 'asset_ids', JSON_ARRAY(c.id))),
       JSON_OBJECT('migrated', TRUE, 'effect_count', 2),
       JSON_OBJECT('blockers', JSON_ARRAY(), 'warnings', JSON_ARRAY()),
       1, c.schema_fingerprint, CURRENT_TIMESTAMP
FROM semantic_columns c
JOIN JSON_TABLE(COALESCE(c.visible_user_ids, JSON_ARRAY()), '$[*]'
    COLUMNS(subject_id VARCHAR(64) PATH '$')) subjects;

INSERT IGNORE INTO semantic_access_policy_effects (
    workspace_id, datasource_id, policy_id, effect_key, subject_type, subject_id,
    asset_type, asset_id, effect_type, condition_json, priority, enabled, policy_version
)
SELECT p.workspace_id, p.datasource_id, p.id, 'restricted', 'scope', '*',
       'column', c.id, 'column_restricted', JSON_OBJECT(), 100, 1, p.policy_version
FROM semantic_access_policies p
JOIN semantic_columns c ON p.workspace_id COLLATE utf8mb4_unicode_ci = c.workspace_id COLLATE utf8mb4_unicode_ci AND p.datasource_id = c.datasource_id
 AND p.source_ref COLLATE utf8mb4_unicode_ci LIKE CONVERT(CONCAT('legacy:column:', c.id, ':%') USING utf8mb4) COLLATE utf8mb4_unicode_ci
UNION ALL
SELECT p.workspace_id, p.datasource_id, p.id, 'allow',
       JSON_UNQUOTE(JSON_EXTRACT(p.subject_json, '$[0].type')),
       JSON_UNQUOTE(JSON_EXTRACT(p.subject_json, '$[0].id')),
       'column', c.id, 'column_allow_sensitive', JSON_OBJECT(), 100, 1, p.policy_version
FROM semantic_access_policies p
JOIN semantic_columns c ON p.workspace_id COLLATE utf8mb4_unicode_ci = c.workspace_id COLLATE utf8mb4_unicode_ci AND p.datasource_id = c.datasource_id
 AND p.source_ref COLLATE utf8mb4_unicode_ci LIKE CONVERT(CONCAT('legacy:column:', c.id, ':%') USING utf8mb4) COLLATE utf8mb4_unicode_ci;

-- Metric visibility lists are migrated as metric restrictions and allows.
INSERT IGNORE INTO semantic_access_policies (
    workspace_id, datasource_id, name, description, source_type, source_ref, status,
    subject_json, asset_selector_json, constraint_json, compile_summary_json,
    validation_json, policy_version, schema_fingerprint, activated_at
)
SELECT m.workspace_id, m.datasource_id, CONCAT('迁移指标权限：', m.business_name),
       '由旧指标可见角色自动迁移。', 'legacy_import',
       CONCAT('legacy:metric:', m.id, ':role:', subjects.subject_id), 'active',
       JSON_ARRAY(JSON_OBJECT('type', 'role', 'id', subjects.subject_id)),
       JSON_OBJECT('metric_ids', JSON_ARRAY(m.id)),
       JSON_ARRAY(JSON_OBJECT('effect', 'metric_allow', 'asset_type', 'metric', 'asset_ids', JSON_ARRAY(m.id))),
       JSON_OBJECT('migrated', TRUE, 'effect_count', 2),
       JSON_OBJECT('blockers', JSON_ARRAY(), 'warnings', JSON_ARRAY()),
       1, m.schema_fingerprint, CURRENT_TIMESTAMP
FROM semantic_metrics m
JOIN JSON_TABLE(COALESCE(m.visible_role_ids, JSON_ARRAY()), '$[*]'
    COLUMNS(subject_id VARCHAR(64) PATH '$')) subjects
UNION ALL
SELECT m.workspace_id, m.datasource_id, CONCAT('迁移指标权限：', m.business_name),
       '由旧指标可见用户自动迁移。', 'legacy_import',
       CONCAT('legacy:metric:', m.id, ':user:', subjects.subject_id), 'active',
       JSON_ARRAY(JSON_OBJECT('type', 'user', 'id', subjects.subject_id)),
       JSON_OBJECT('metric_ids', JSON_ARRAY(m.id)),
       JSON_ARRAY(JSON_OBJECT('effect', 'metric_allow', 'asset_type', 'metric', 'asset_ids', JSON_ARRAY(m.id))),
       JSON_OBJECT('migrated', TRUE, 'effect_count', 2),
       JSON_OBJECT('blockers', JSON_ARRAY(), 'warnings', JSON_ARRAY()),
       1, m.schema_fingerprint, CURRENT_TIMESTAMP
FROM semantic_metrics m
JOIN JSON_TABLE(COALESCE(m.visible_user_ids, JSON_ARRAY()), '$[*]'
    COLUMNS(subject_id VARCHAR(64) PATH '$')) subjects;

INSERT IGNORE INTO semantic_access_policy_effects (
    workspace_id, datasource_id, policy_id, effect_key, subject_type, subject_id,
    asset_type, asset_id, effect_type, condition_json, priority, enabled, policy_version
)
SELECT p.workspace_id, p.datasource_id, p.id, 'restricted', 'scope', '*',
       'metric', m.id, 'metric_restricted', JSON_OBJECT(), 100, 1, p.policy_version
FROM semantic_access_policies p
JOIN semantic_metrics m ON p.workspace_id COLLATE utf8mb4_unicode_ci = m.workspace_id COLLATE utf8mb4_unicode_ci AND p.datasource_id = m.datasource_id
 AND p.source_ref COLLATE utf8mb4_unicode_ci LIKE CONVERT(CONCAT('legacy:metric:', m.id, ':%') USING utf8mb4) COLLATE utf8mb4_unicode_ci
UNION ALL
SELECT p.workspace_id, p.datasource_id, p.id, 'allow',
       JSON_UNQUOTE(JSON_EXTRACT(p.subject_json, '$[0].type')),
       JSON_UNQUOTE(JSON_EXTRACT(p.subject_json, '$[0].id')),
       'metric', m.id, 'metric_allow', JSON_OBJECT(), 100, 1, p.policy_version
FROM semantic_access_policies p
JOIN semantic_metrics m ON p.workspace_id COLLATE utf8mb4_unicode_ci = m.workspace_id COLLATE utf8mb4_unicode_ci AND p.datasource_id = m.datasource_id
 AND p.source_ref COLLATE utf8mb4_unicode_ci LIKE CONVERT(CONCAT('legacy:metric:', m.id, ':%') USING utf8mb4) COLLATE utf8mb4_unicode_ci;

-- Sensitive objects with no legacy allow list remain explicitly denied.
INSERT IGNORE INTO semantic_access_policies (
    workspace_id, datasource_id, name, description, source_type, source_ref, status,
    subject_json, asset_selector_json, constraint_json, compile_summary_json,
    validation_json, policy_version, schema_fingerprint, activated_at
)
SELECT workspace_id, datasource_id, CONCAT('迁移敏感表保护：', business_name),
       '敏感表没有旧授权名单，默认拒绝所有非管理员。', 'legacy_import',
       CONCAT('legacy:sensitive-table:', id), 'active', JSON_ARRAY(JSON_OBJECT('type', 'all', 'id', '*')),
       JSON_OBJECT('table_ids', JSON_ARRAY(id)),
       JSON_ARRAY(JSON_OBJECT('effect', 'table_deny', 'asset_type', 'table', 'asset_ids', JSON_ARRAY(id))),
       JSON_OBJECT('migrated', TRUE, 'effect_count', 1),
       JSON_OBJECT('blockers', JSON_ARRAY(), 'warnings', JSON_ARRAY()),
       1, schema_fingerprint, CURRENT_TIMESTAMP
FROM semantic_tables
WHERE is_sensitive = 1
  AND JSON_LENGTH(COALESCE(visible_role_ids, JSON_ARRAY())) = 0
  AND JSON_LENGTH(COALESCE(visible_user_ids, JSON_ARRAY())) = 0
UNION ALL
SELECT workspace_id, datasource_id, CONCAT('迁移敏感字段保护：', business_name),
       '敏感字段没有旧授权名单，默认拒绝所有非管理员。', 'legacy_import',
       CONCAT('legacy:sensitive-column:', id), 'active', JSON_ARRAY(JSON_OBJECT('type', 'all', 'id', '*')),
       JSON_OBJECT('column_ids', JSON_ARRAY(id)),
       JSON_ARRAY(JSON_OBJECT('effect', 'column_hide', 'asset_type', 'column', 'asset_ids', JSON_ARRAY(id))),
       JSON_OBJECT('migrated', TRUE, 'effect_count', 1),
       JSON_OBJECT('blockers', JSON_ARRAY(), 'warnings', JSON_ARRAY()),
       1, schema_fingerprint, CURRENT_TIMESTAMP
FROM semantic_columns
WHERE is_sensitive = 1
  AND JSON_LENGTH(COALESCE(visible_role_ids, JSON_ARRAY())) = 0
  AND JSON_LENGTH(COALESCE(visible_user_ids, JSON_ARRAY())) = 0
UNION ALL
SELECT workspace_id, datasource_id, CONCAT('迁移敏感指标保护：', business_name),
       '敏感指标没有旧授权名单，默认拒绝所有非管理员。', 'legacy_import',
       CONCAT('legacy:sensitive-metric:', id), 'active', JSON_ARRAY(JSON_OBJECT('type', 'all', 'id', '*')),
       JSON_OBJECT('metric_ids', JSON_ARRAY(id)),
       JSON_ARRAY(JSON_OBJECT('effect', 'metric_hide', 'asset_type', 'metric', 'asset_ids', JSON_ARRAY(id))),
       JSON_OBJECT('migrated', TRUE, 'effect_count', 1),
       JSON_OBJECT('blockers', JSON_ARRAY(), 'warnings', JSON_ARRAY()),
       1, schema_fingerprint, CURRENT_TIMESTAMP
FROM semantic_metrics
WHERE is_sensitive = 1
  AND JSON_LENGTH(COALESCE(visible_role_ids, JSON_ARRAY())) = 0
  AND JSON_LENGTH(COALESCE(visible_user_ids, JSON_ARRAY())) = 0;

INSERT IGNORE INTO semantic_access_policy_effects (
    workspace_id, datasource_id, policy_id, effect_key, subject_type, subject_id,
    asset_type, asset_id, effect_type, condition_json, priority, enabled, policy_version
)
SELECT p.workspace_id, p.datasource_id, p.id, 'deny', 'all', '*', 'table', t.id,
       'table_deny', JSON_OBJECT(), 1000, 1, p.policy_version
FROM semantic_access_policies p
JOIN semantic_tables t
  ON p.workspace_id COLLATE utf8mb4_unicode_ci = t.workspace_id COLLATE utf8mb4_unicode_ci
 AND p.datasource_id = t.datasource_id
 AND p.source_ref COLLATE utf8mb4_unicode_ci = CONVERT(CONCAT('legacy:sensitive-table:', t.id) USING utf8mb4) COLLATE utf8mb4_unicode_ci
UNION ALL
SELECT p.workspace_id, p.datasource_id, p.id, 'deny', 'all', '*', 'column', c.id,
       'column_hide', JSON_OBJECT(), 1000, 1, p.policy_version
FROM semantic_access_policies p
JOIN semantic_columns c
  ON p.workspace_id COLLATE utf8mb4_unicode_ci = c.workspace_id COLLATE utf8mb4_unicode_ci
 AND p.datasource_id = c.datasource_id
 AND p.source_ref COLLATE utf8mb4_unicode_ci = CONVERT(CONCAT('legacy:sensitive-column:', c.id) USING utf8mb4) COLLATE utf8mb4_unicode_ci
UNION ALL
SELECT p.workspace_id, p.datasource_id, p.id, 'deny', 'all', '*', 'metric', m.id,
       'metric_hide', JSON_OBJECT(), 1000, 1, p.policy_version
FROM semantic_access_policies p
JOIN semantic_metrics m
  ON p.workspace_id COLLATE utf8mb4_unicode_ci = m.workspace_id COLLATE utf8mb4_unicode_ci
 AND p.datasource_id = m.datasource_id
 AND p.source_ref COLLATE utf8mb4_unicode_ci = CONVERT(CONCAT('legacy:sensitive-metric:', m.id) USING utf8mb4) COLLATE utf8mb4_unicode_ci;
