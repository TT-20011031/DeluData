-- Regression evaluation runs for first-stage semantic NL2SQL rollout.

CREATE TABLE IF NOT EXISTS semantic_evaluation_runs (
    id INT PRIMARY KEY AUTO_INCREMENT,
    workspace_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NULL,
    case_id VARCHAR(32) NOT NULL,
    question TEXT NOT NULL,
    test_dimension VARCHAR(255) NOT NULL DEFAULT '',
    expected_focus JSON NOT NULL,
    expected_limit INT NULL,
    semantic_result JSON NOT NULL,
    legacy_result JSON NOT NULL,
    verdict VARCHAR(32) NOT NULL DEFAULT 'unknown',
    diagnostics JSON NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_semantic_eval_workspace (workspace_id),
    INDEX idx_semantic_eval_user (user_id),
    INDEX idx_semantic_eval_case (case_id),
    INDEX idx_semantic_eval_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
