"""
创建/校正 document_images 表结构

目标：
1. 确保存在 image_id 字段（用于 file_id + image_id 唯一定位）。
2. 确保存在唯一索引 uq_document_images_file_image(file_id, image_id)。
3. 确保存在 files(id) 外键（ON DELETE CASCADE）。

用法：
    cd backend
    python -m migrations.run_document_images_migration
"""
from __future__ import annotations

import asyncio
import os
import platform
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db.database import get_async_db_manager


TABLE_NAME = "document_images"
FK_NAME = "fk_document_images_file"
UNIQUE_INDEX_NAME = "uq_document_images_file_image"
FILE_INDEX_NAME = "ix_document_images_file_id"
PHASH_INDEX_NAME = "ix_document_images_phash"


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


async def _column_exists(session, table_name: str, column_name: str) -> bool:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
              AND column_name = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return int(result.scalar() or 0) > 0


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


async def _foreign_key_exists(session, table_name: str, fk_name: str) -> bool:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.table_constraints
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
              AND constraint_name = :fk_name
              AND constraint_type = 'FOREIGN KEY'
            """
        ),
        {"table_name": table_name, "fk_name": fk_name},
    )
    return int(result.scalar() or 0) > 0


async def _has_any_file_fk(session, table_name: str) -> bool:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.key_column_usage
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
              AND column_name = 'file_id'
              AND referenced_table_name = 'files'
              AND referenced_column_name = 'id'
            """
        ),
        {"table_name": table_name},
    )
    return int(result.scalar() or 0) > 0


async def _create_table(session) -> None:
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS document_images (
                id VARCHAR(36) PRIMARY KEY,
                file_id VARCHAR(36) NOT NULL,
                image_id VARCHAR(64) NOT NULL,
                storage_path VARCHAR(512) NOT NULL,
                page_number INT DEFAULT 1,
                bbox VARCHAR(100),
                alt_text TEXT,
                phash VARCHAR(64),
                width INT DEFAULT 0,
                height INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX ix_document_images_file_id (file_id),
                INDEX ix_document_images_phash (phash),
                UNIQUE KEY uq_document_images_file_image (file_id, image_id),
                CONSTRAINT fk_document_images_file
                  FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )


async def _print_indexes(session) -> None:
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
        {"table_name": TABLE_NAME},
    )
    print("当前索引：")
    for index_name, non_unique, columns_joined in result.fetchall():
        uniq_text = "UNIQUE" if int(non_unique or 1) == 0 else "INDEX"
        print(f"  - {index_name} ({uniq_text}): {columns_joined}")


async def run_migration() -> None:
    print("=" * 80)
    print("DocumentImages Migration")
    print("=" * 80)

    db = get_async_db_manager()
    async with db.session_scope() as session:
        table_exists = await _table_exists(session, TABLE_NAME)
        if not table_exists:
            await _create_table(session)
            print(f"[OK] 已创建表: {TABLE_NAME}")
            await _print_indexes(session)
            print("=" * 80)
            print("迁移完成")
            print("=" * 80)
            return

        print(f"[INFO] 表已存在，开始校正: {TABLE_NAME}")

        if not await _column_exists(session, TABLE_NAME, "image_id"):
            await session.execute(
                text(
                    """
                    ALTER TABLE document_images
                    ADD COLUMN image_id VARCHAR(64) NULL AFTER file_id
                    """
                )
            )
            print("[OK] 已新增列: image_id")
        else:
            print("[SKIP] 列已存在: image_id")

        await session.execute(
            text(
                """
                UPDATE document_images
                SET image_id = id
                WHERE image_id IS NULL OR image_id = ''
                """
            )
        )
        print("[OK] 已回填 image_id")

        await session.execute(
            text(
                """
                ALTER TABLE document_images
                MODIFY COLUMN image_id VARCHAR(64) NOT NULL
                """
            )
        )
        print("[OK] 已校正 image_id 为 NOT NULL")

        # 清理 (file_id, image_id) 重复记录，保留 id 最小的一条
        await session.execute(
            text(
                """
                DELETE di1
                FROM document_images di1
                JOIN document_images di2
                  ON di1.file_id = di2.file_id
                 AND di1.image_id = di2.image_id
                 AND di1.id > di2.id
                """
            )
        )
        print("[OK] 已清理重复记录")

        if not await _index_exists(session, TABLE_NAME, FILE_INDEX_NAME):
            await session.execute(
                text(
                    f"""
                    ALTER TABLE {_quote_identifier(TABLE_NAME)}
                    ADD INDEX {_quote_identifier(FILE_INDEX_NAME)} (file_id)
                    """
                )
            )
            print(f"[OK] 已创建索引: {FILE_INDEX_NAME}")
        else:
            print(f"[SKIP] 索引已存在: {FILE_INDEX_NAME}")

        if not await _index_exists(session, TABLE_NAME, PHASH_INDEX_NAME):
            await session.execute(
                text(
                    f"""
                    ALTER TABLE {_quote_identifier(TABLE_NAME)}
                    ADD INDEX {_quote_identifier(PHASH_INDEX_NAME)} (phash)
                    """
                )
            )
            print(f"[OK] 已创建索引: {PHASH_INDEX_NAME}")
        else:
            print(f"[SKIP] 索引已存在: {PHASH_INDEX_NAME}")

        if not await _index_exists(session, TABLE_NAME, UNIQUE_INDEX_NAME):
            await session.execute(
                text(
                    f"""
                    ALTER TABLE {_quote_identifier(TABLE_NAME)}
                    ADD UNIQUE KEY {_quote_identifier(UNIQUE_INDEX_NAME)} (file_id, image_id)
                    """
                )
            )
            print(f"[OK] 已创建唯一索引: {UNIQUE_INDEX_NAME}")
        else:
            print(f"[SKIP] 唯一索引已存在: {UNIQUE_INDEX_NAME}")

        has_file_fk = await _has_any_file_fk(session, TABLE_NAME)
        if not has_file_fk and not await _foreign_key_exists(session, TABLE_NAME, FK_NAME):
            await session.execute(
                text(
                    f"""
                    ALTER TABLE {_quote_identifier(TABLE_NAME)}
                    ADD CONSTRAINT {_quote_identifier(FK_NAME)}
                    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
                    """
                )
            )
            print(f"[OK] 已创建外键: {FK_NAME}")
        else:
            print("[SKIP] file_id -> files.id 外键已存在")

        await _print_indexes(session)

    print("=" * 80)
    print("迁移完成")
    print("=" * 80)


async def _main() -> None:
    try:
        await run_migration()
    finally:
        # 显式释放异步连接池，避免 Windows 下 event loop 关闭时的 aiomysql __del__ 警告
        try:
            await get_async_db_manager().dispose()
        except Exception:
            pass


if __name__ == "__main__":
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_main())
