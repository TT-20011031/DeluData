-- PageIndex: tree_nodes 索引表

CREATE TABLE IF NOT EXISTS tree_nodes (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,

    file_id VARCHAR(36) NOT NULL,
    workspace_id VARCHAR(36) NOT NULL,

    node_id VARCHAR(64) NOT NULL,
    parent_node_id VARCHAR(64) NULL,

    title VARCHAR(500) NOT NULL,
    summary TEXT NULL,
    content LONGTEXT NULL,

    start_page INT NULL,
    end_page INT NULL,

    node_level INT NOT NULL DEFAULT 0,
    sort_order INT NOT NULL DEFAULT 0,
    token_count INT NOT NULL DEFAULT 0,

    visibility VARCHAR(20) NOT NULL DEFAULT 'dept',
    owner_id VARCHAR(36) NULL,
    dept_id INT NULL,

    meta_info JSON NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_file_node (file_id, node_id),
    KEY idx_ws_file (workspace_id, file_id),
    KEY idx_parent (file_id, parent_node_id),
    KEY idx_level_sort (file_id, node_level, sort_order),
    KEY idx_visibility (workspace_id, visibility),
    KEY idx_owner (owner_id),
    KEY idx_dept (dept_id),
    CONSTRAINT fk_tree_nodes_file_id FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- PageIndex: files 状态字段
ALTER TABLE files
    ADD COLUMN IF NOT EXISTS pageindex_status VARCHAR(20) NULL,
    ADD COLUMN IF NOT EXISTS pageindex_error TEXT NULL;

