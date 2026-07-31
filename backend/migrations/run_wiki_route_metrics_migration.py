"""[M3.5] 创建 wiki_route_metrics 表（Wiki 检索路由评估指标）。

用法：
    cd backend
    python -m migrations.run_wiki_route_metrics_migration

幂等：使用 CREATE TABLE IF NOT EXISTS。
"""
from __future__ import annotations

import asyncio
import os
import platform
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db.database import get_async_db_manager


DDL = """
CREATE TABLE IF NOT EXISTS wiki_route_metrics (
    id VARCHAR(36) PRIMARY KEY,
    workspace_id VARCHAR(36) NOT NULL DEFAULT 'default' COMMENT '租户ID',
    session_id VARCHAR(64) NULL COMMENT '会话 id',
    message_id VARCHAR(64) NULL COMMENT '消息 id',
    user_id VARCHAR(36) NULL COMMENT '发起用户 id',
    knowledge_path VARCHAR(16) NOT NULL DEFAULT 'rag' COMMENT 'rag/wiki/both',
    knowledge_path_source VARCHAR(16) NULL COMMENT 'rule/llm/fallback/disabled/default',
    knowledge_path_reason VARCHAR(255) NULL COMMENT '判定原因摘要',
    chunks_used INT NOT NULL DEFAULT 0 COMMENT '送入 Synthesizer 的总 chunk 数',
    wiki_chunks_count INT NOT NULL DEFAULT 0 COMMENT 'navigator 装载的 wiki_page 数',
    wiki_chunks_used INT NOT NULL DEFAULT 0 COMMENT '实际被使用的 wiki_page 数',
    has_wiki_gap TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否触发 wiki_gap 入队',
    wiki_gap_task_id VARCHAR(36) NULL COMMENT '对应 wiki_compile_tasks.id',
    latency_ms INT NOT NULL DEFAULT 0 COMMENT 'doc_worker 端到端时延（ms）',
    stop_reason VARCHAR(32) NULL COMMENT 'rag_ok / rag_empty / rag_retrieval_error',
    user_query TEXT NULL COMMENT '用户查询前 200 字（脱敏后）',
    extra_meta JSON NULL COMMENT '扩展字段（模型/温度/特性开关等）',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX ix_wiki_route_metrics_workspace (workspace_id),
    INDEX ix_wiki_route_metrics_ws_created (workspace_id, created_at),
    INDEX ix_wiki_route_metrics_ws_path_created (workspace_id, knowledge_path, created_at),
    INDEX ix_wiki_route_metrics_ws_session (workspace_id, session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='[M3.5] Wiki 检索路由评估指标'
"""


async def run_migration() -> None:
    print("=" * 80)
    print("Wiki Route Metrics Migration (M3.5)")
    print("=" * 80)

    db = get_async_db_manager()
    async with db.session_scope() as session:
        await session.execute(text(DDL))
        print(f"[OK] CREATE TABLE IF NOT EXISTS wiki_route_metrics")

    print("=" * 80)
    print("wiki_route_metrics 表迁移完成")
    print("=" * 80)


if __name__ == "__main__":
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_migration())
