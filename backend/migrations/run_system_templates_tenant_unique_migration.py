"""
修复 system_templates 多租户唯一索引

目标：
1. 删除旧的全局唯一索引 (template_id)
2. 增加租户内复合唯一索引 (workspace_id, template_id)
3. 检查并阻断脏数据（同租户同 template_id 重复）

用法：
    cd backend
    python -m migrations.run_system_templates_tenant_unique_migration
"""
from __future__ import annotations

import asyncio
import os
import platform
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db.database import get_async_db_manager


TARGET_TABLE = "system_templates"
TARGET_INDEX = "uq_system_templates_workspace_template"
LEGACY_INDEX_NAMES = {"uk_template_id", "ix_system_templates_template_id"}


def _quote_identifier(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


async def _table_exists(session, table_name: str) -> bool:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
            """
        ),
        {"table_name": table_name},
    )
    return int(result.scalar() or 0) > 0


async def _get_indexes(session, table_name: str):
    result = await session.execute(
        text(
            """
            SELECT
                INDEX_NAME,
                NON_UNIQUE,
                GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns_joined
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
            GROUP BY INDEX_NAME, NON_UNIQUE
            ORDER BY INDEX_NAME
            """
        ),
        {"table_name": table_name},
    )
    return result.fetchall()


async def _index_exists(session, table_name: str, index_name: str) -> bool:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
              AND index_name = :index_name
            """
        ),
        {"table_name": table_name, "index_name": index_name},
    )
    return int(result.scalar() or 0) > 0


async def _check_duplicate_rows(session) -> list[tuple[str, str, int]]:
    result = await session.execute(
        text(
            """
            SELECT workspace_id, template_id, COUNT(*) AS cnt
            FROM system_templates
            GROUP BY workspace_id, template_id
            HAVING COUNT(*) > 1
            ORDER BY cnt DESC
            LIMIT 20
            """
        )
    )
    return [(str(r[0]), str(r[1]), int(r[2])) for r in result.fetchall()]


async def run_migration() -> None:
    print("=" * 80)
    print("SystemTemplates Tenant Unique Migration")
    print("=" * 80)

    db = get_async_db_manager()
    async with db.session_scope() as session:
        if not await _table_exists(session, TARGET_TABLE):
            print(f"[SKIP] {TARGET_TABLE} 不存在，无需迁移")
            return

        duplicates = await _check_duplicate_rows(session)
        if duplicates:
            print("[FAIL] 检测到重复数据，迁移已中止。请先清理以下记录：")
            for workspace_id, template_id, count in duplicates:
                print(
                    f"  workspace_id={workspace_id}, template_id={template_id}, count={count}"
                )
            raise RuntimeError("存在重复数据 (workspace_id, template_id)")

        # 优先按历史索引名删除（兼容历史脚本直接创建的唯一索引）
        dropped = 0
        for legacy_index_name in sorted(LEGACY_INDEX_NAMES):
            if await _index_exists(session, TARGET_TABLE, legacy_index_name):
                await session.execute(
                    text(
                        f"ALTER TABLE {_quote_identifier(TARGET_TABLE)} "
                        f"DROP INDEX {_quote_identifier(legacy_index_name)}"
                    )
                )
                dropped += 1
                print(f"[OK] 已删除旧唯一索引: {legacy_index_name}")

        indexes = await _get_indexes(session, TARGET_TABLE)
        for index_name, non_unique, columns_joined in indexes:
            name = str(index_name)
            columns = str(columns_joined or "")
            is_unique = int(non_unique or 1) == 0
            if name == "PRIMARY" or not is_unique:
                continue

            legacy_match = name in LEGACY_INDEX_NAMES
            single_template_unique = columns == "template_id"
            if legacy_match or single_template_unique:
                await session.execute(
                    text(
                        f"ALTER TABLE {_quote_identifier(TARGET_TABLE)} "
                        f"DROP INDEX {_quote_identifier(name)}"
                    )
                )
                dropped += 1
                print(f"[OK] 已删除旧唯一索引: {name} ({columns})")

        if dropped == 0:
            print("[SKIP] 未发现需删除的旧唯一索引")

        if not await _index_exists(session, TARGET_TABLE, TARGET_INDEX):
            await session.execute(
                text(
                    """
                    ALTER TABLE system_templates
                    ADD UNIQUE KEY uq_system_templates_workspace_template
                    (workspace_id, template_id)
                    """
                )
            )
            print(f"[OK] 已创建复合唯一索引: {TARGET_INDEX}(workspace_id, template_id)")
        else:
            print(f"[SKIP] 复合唯一索引已存在: {TARGET_INDEX}")

        final_indexes = await session.execute(
            text(
                """
                SELECT
                    INDEX_NAME,
                    GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns_joined
                FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                  AND table_name = :table_name
                  AND NON_UNIQUE = 0
                GROUP BY INDEX_NAME
                ORDER BY INDEX_NAME
                """
            ),
            {"table_name": TARGET_TABLE},
        )

        print("-" * 80)
        print("当前唯一索引:")
        for index_name, columns_joined in final_indexes.fetchall():
            print(f"  - {index_name}: {columns_joined}")

    print("=" * 80)
    print("迁移完成")
    print("=" * 80)


if __name__ == "__main__":
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_migration())
