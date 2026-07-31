-- Skills 操作手册表
-- 用于存储用户自定义的操作指南，供 Planner 检索参考

CREATE TABLE IF NOT EXISTS `skills` (
    `id` VARCHAR(36) NOT NULL COMMENT 'UUID 主键',
    `workspace_id` VARCHAR(36) NULL COMMENT '所属工作区ID',
    `title` VARCHAR(255) NOT NULL COMMENT '技能标题（用于展示和检索）',
    `description` TEXT NOT NULL COMMENT '技能描述（用于语义检索）',
    `steps` JSON NOT NULL COMMENT '步骤列表 [{step, action, tool, template, keywords}]',
    `tags` JSON DEFAULT NULL COMMENT '标签列表，用于分类和检索',
    `example_queries` JSON DEFAULT NULL COMMENT '触发此 Skill 的典型用户提问，用于提高检索召回率',
    `visibility` ENUM('global', 'workspace') NOT NULL DEFAULT 'workspace' COMMENT '可见性: global/workspace',
    `usage_count` INT NOT NULL DEFAULT 0 COMMENT '使用次数，用于热门排序',
    `created_by` VARCHAR(36) NOT NULL COMMENT '创建者用户ID',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    INDEX `idx_workspace_id` (`workspace_id`),
    INDEX `idx_title` (`title`),
    INDEX `idx_visibility` (`visibility`),
    CONSTRAINT `fk_skills_workspace` FOREIGN KEY (`workspace_id`) REFERENCES `workspaces` (`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_skills_user` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Skill 操作手册表';
