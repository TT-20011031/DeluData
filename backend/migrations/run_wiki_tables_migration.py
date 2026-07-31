"""
创建 Wiki 五张表（Karpathy LLM Wiki 模式）。

用法：
    cd backend
    python -m migrations.run_wiki_tables_migration
"""
from __future__ import annotations

import asyncio
import os
import platform
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db.database import get_async_db_manager


# 顺序敏感：page → page_source / link / revision / compile_task
DDL_STATEMENTS: list[str] = [
    # ===== wiki_pages =====
    """
    CREATE TABLE IF NOT EXISTS wiki_pages (
        id VARCHAR(36) PRIMARY KEY,
        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default' COMMENT '租户ID',
        slug VARCHAR(255) NOT NULL COMMENT '工作区内唯一的 URL 标识',
        title VARCHAR(255) NOT NULL COMMENT '实体显示名',
        aliases JSON NULL COMMENT '同名/别名 JSON 数组',
        domain VARCHAR(64) NOT NULL DEFAULT 'general' COMMENT '知识域分类',
        summary TEXT NULL COMMENT '一句话摘要',
        markdown_body MEDIUMTEXT NOT NULL COMMENT '完整 Markdown 正文',
        status VARCHAR(16) NOT NULL DEFAULT 'draft' COMMENT 'draft/published/archived',
        version INT NOT NULL DEFAULT 1 COMMENT '版本号（乐观锁）',
        char_count BIGINT NOT NULL DEFAULT 0,
        token_count BIGINT NOT NULL DEFAULT 0,
        last_compiled_at DATETIME NULL,
        last_compiled_by VARCHAR(64) NULL,
        compile_meta JSON NULL,
        owner_id VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_wiki_pages_ws_slug (workspace_id, slug),
        INDEX ix_wiki_pages_workspace (workspace_id),
        INDEX ix_wiki_pages_ws_domain (workspace_id, domain),
        INDEX ix_wiki_pages_ws_status (workspace_id, status),
        INDEX ix_wiki_pages_ws_compiled (workspace_id, last_compiled_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    COMMENT='Wiki 实体页（Karpathy LLM Wiki 模式）'
    """,
    # ===== wiki_page_sources =====
    """
    CREATE TABLE IF NOT EXISTS wiki_page_sources (
        id VARCHAR(36) PRIMARY KEY,
        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
        page_id VARCHAR(36) NOT NULL,
        file_id VARCHAR(36) NOT NULL,
        chunk_ids JSON NULL,
        excerpt TEXT NULL,
        relevance DOUBLE NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_wiki_page_sources_ws_page_file (workspace_id, page_id, file_id),
        INDEX ix_wiki_page_sources_workspace (workspace_id),
        INDEX ix_wiki_page_sources_ws_page (workspace_id, page_id),
        INDEX ix_wiki_page_sources_ws_file (workspace_id, file_id),
        CONSTRAINT fk_wiki_page_sources_page FOREIGN KEY (page_id)
            REFERENCES wiki_pages(id) ON DELETE CASCADE,
        CONSTRAINT fk_wiki_page_sources_file FOREIGN KEY (file_id)
            REFERENCES files(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    COMMENT='Wiki 实体页 ↔ 原文档/切片溯源'
    """,
    # ===== wiki_links =====
    """
    CREATE TABLE IF NOT EXISTS wiki_links (
        id VARCHAR(36) PRIMARY KEY,
        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
        source_page_id VARCHAR(36) NOT NULL,
        target_page_id VARCHAR(36) NOT NULL,
        link_type VARCHAR(32) NOT NULL DEFAULT 'mentions',
        evidence_chunk_ids JSON NULL,
        confidence DOUBLE NOT NULL DEFAULT 1.0,
        note VARCHAR(512) NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        extra_meta JSON NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_wiki_links_triplet (workspace_id, source_page_id, target_page_id, link_type),
        INDEX ix_wiki_links_workspace (workspace_id),
        INDEX ix_wiki_links_ws_source (workspace_id, source_page_id),
        INDEX ix_wiki_links_ws_target (workspace_id, target_page_id),
        INDEX ix_wiki_links_ws_type (workspace_id, link_type),
        CONSTRAINT fk_wiki_links_source FOREIGN KEY (source_page_id)
            REFERENCES wiki_pages(id) ON DELETE CASCADE,
        CONSTRAINT fk_wiki_links_target FOREIGN KEY (target_page_id)
            REFERENCES wiki_pages(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    COMMENT='Wiki 实体页之间的双向链接'
    """,
    # ===== wiki_revisions =====
    """
    CREATE TABLE IF NOT EXISTS wiki_revisions (
        id VARCHAR(36) PRIMARY KEY,
        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
        page_id VARCHAR(36) NOT NULL,
        version INT NOT NULL,
        committed_by VARCHAR(64) NOT NULL DEFAULT 'agent',
        commit_message VARCHAR(512) NULL,
        snapshot_markdown MEDIUMTEXT NOT NULL,
        snapshot_summary TEXT NULL,
        diff_text MEDIUMTEXT NULL,
        extra_meta JSON NULL,
        committed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX ix_wiki_revisions_workspace (workspace_id),
        INDEX ix_wiki_revisions_ws_page_ver (workspace_id, page_id, version),
        INDEX ix_wiki_revisions_ws_committed (workspace_id, committed_at),
        CONSTRAINT fk_wiki_revisions_page FOREIGN KEY (page_id)
            REFERENCES wiki_pages(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    COMMENT='Wiki 实体页修订历史'
    """,
    # ===== wiki_compile_tasks =====
    """
    CREATE TABLE IF NOT EXISTS wiki_compile_tasks (
        id VARCHAR(36) PRIMARY KEY,
        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
        trigger_type VARCHAR(32) NOT NULL DEFAULT 'manual',
        user_id VARCHAR(36) NULL,
        payload_json JSON NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        stage VARCHAR(32) NOT NULL DEFAULT 'queued',
        progress INT NOT NULL DEFAULT 0,
        detail_json JSON NULL,
        error_message TEXT NULL,
        result_json JSON NULL,
        attempt INT NOT NULL DEFAULT 0,
        max_attempts INT NOT NULL DEFAULT 2,
        worker_id VARCHAR(128) NULL,
        run_token VARCHAR(64) NULL,
        lease_expires_at DATETIME NULL,
        heartbeat_at DATETIME NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        started_at DATETIME NULL,
        finished_at DATETIME NULL,
        INDEX ix_wiki_compile_tasks_workspace (workspace_id),
        INDEX ix_wiki_compile_tasks_status_lease (status, lease_expires_at),
        INDEX ix_wiki_compile_tasks_ws_status_created (workspace_id, status, created_at),
        INDEX ix_wiki_compile_tasks_ws_trigger (workspace_id, trigger_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    COMMENT='Wiki 编译任务队列'
    """,
]


async def run_migration() -> None:
    print("=" * 80)
    print("Wiki Tables Migration (Karpathy LLM Wiki 模式)")
    print("=" * 80)

    db = get_async_db_manager()
    async with db.session_scope() as session:
        for ddl in DDL_STATEMENTS:
            await session.execute(text(ddl))
            preview = ddl.strip().split("\n", 1)[0][:120]
            print(f"[OK] {preview}")

    print("=" * 80)
    print("Wiki 表迁移完成")
    print("=" * 80)


if __name__ == "__main__":
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_migration())
