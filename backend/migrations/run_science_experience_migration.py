"""
Science experience migration runner.

Usage:
    cd backend
    python -m migrations.run_science_experience_migration
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db.database import get_async_db_manager


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def _skip(msg: str) -> None:
    print(f"[SKIP] {msg}")


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


async def _constraint_exists(session, table_name: str, constraint_name: str) -> bool:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.table_constraints
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
              AND constraint_name = :constraint_name
            """
        ),
        {"table_name": table_name, "constraint_name": constraint_name},
    )
    return int(result.scalar() or 0) > 0


async def run_migration() -> None:
    print("=" * 72)
    print("Science Experience Migration")
    print("=" * 72)

    db = get_async_db_manager()
    async with db.session_scope() as session:
        # -------------------------------------------------------------
        # Museum legacy tenant fields
        # -------------------------------------------------------------
        if not await _column_exists(session, "museum_products", "workspace_id"):
            await session.execute(
                text(
                    """
                    ALTER TABLE museum_products
                    ADD COLUMN workspace_id VARCHAR(36) NOT NULL DEFAULT 'default'
                    """
                )
            )
            _ok("museum_products.workspace_id added")
        else:
            _skip("museum_products.workspace_id already exists")

        if not await _index_exists(
            session, "museum_products", "idx_museum_products_workspace_status"
        ):
            await session.execute(
                text(
                    """
                    CREATE INDEX idx_museum_products_workspace_status
                    ON museum_products(workspace_id, status)
                    """
                )
            )
            _ok("idx_museum_products_workspace_status created")
        else:
            _skip("idx_museum_products_workspace_status already exists")

        if not await _column_exists(session, "museum_guide_sessions", "workspace_id"):
            await session.execute(
                text(
                    """
                    ALTER TABLE museum_guide_sessions
                    ADD COLUMN workspace_id VARCHAR(36) NOT NULL DEFAULT 'default'
                    """
                )
            )
            _ok("museum_guide_sessions.workspace_id added")
        else:
            _skip("museum_guide_sessions.workspace_id already exists")

        if not await _index_exists(
            session, "museum_guide_sessions", "idx_museum_sessions_workspace_created"
        ):
            await session.execute(
                text(
                    """
                    CREATE INDEX idx_museum_sessions_workspace_created
                    ON museum_guide_sessions(workspace_id, created_at)
                    """
                )
            )
            _ok("idx_museum_sessions_workspace_created created")
        else:
            _skip("idx_museum_sessions_workspace_created already exists")

        if not await _column_exists(session, "museum_artifacts", "workspace_id"):
            await session.execute(
                text(
                    """
                    ALTER TABLE museum_artifacts
                    ADD COLUMN workspace_id VARCHAR(36) NOT NULL DEFAULT 'default'
                    """
                )
            )
            _ok("museum_artifacts.workspace_id added")
        else:
            _skip("museum_artifacts.workspace_id already exists")

        if not await _index_exists(
            session, "museum_artifacts", "idx_museum_artifacts_workspace_name"
        ):
            await session.execute(
                text(
                    """
                    CREATE INDEX idx_museum_artifacts_workspace_name
                    ON museum_artifacts(workspace_id, name)
                    """
                )
            )
            _ok("idx_museum_artifacts_workspace_name created")
        else:
            _skip("idx_museum_artifacts_workspace_name already exists")

        if not await _index_exists(
            session, "museum_artifacts", "idx_museum_artifacts_workspace_highlight"
        ):
            await session.execute(
                text(
                    """
                    CREATE INDEX idx_museum_artifacts_workspace_highlight
                    ON museum_artifacts(workspace_id, is_highlight, click_count)
                    """
                )
            )
            _ok("idx_museum_artifacts_workspace_highlight created")
        else:
            _skip("idx_museum_artifacts_workspace_highlight already exists")

        # -------------------------------------------------------------
        # Science tables
        # -------------------------------------------------------------
        if not await _table_exists(session, "science_devices"):
            await session.execute(
                text(
                    """
                    CREATE TABLE science_devices (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
                        device_id VARCHAR(64) NOT NULL,
                        name VARCHAR(128) NULL,
                        device_token VARCHAR(128) NOT NULL,
                        service_user_id VARCHAR(36) NOT NULL,
                        dept_id INT NULL,
                        kb_scope_mode VARCHAR(16) NOT NULL DEFAULT 'dept',
                        kb_scope_dept_ids_json JSON NULL,
                        kb_scope_file_ids_json JSON NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uq_science_device_workspace (workspace_id, device_id),
                        UNIQUE KEY uq_science_device_token (device_token),
                        KEY idx_science_device_workspace_active (workspace_id, is_active),
                        CONSTRAINT fk_science_device_user
                            FOREIGN KEY (service_user_id) REFERENCES sys_users(id),
                        CONSTRAINT fk_science_device_dept
                            FOREIGN KEY (dept_id) REFERENCES sys_departments(id)
                    )
                    """
                )
            )
            _ok("science_devices created")
        else:
            _skip("science_devices already exists")

        if not await _column_exists(session, "science_devices", "activation_key_hash"):
            await session.execute(
                text(
                    """
                    ALTER TABLE science_devices
                    ADD COLUMN activation_key_hash VARCHAR(128) NULL
                    COMMENT '设备激活码哈希'
                    """
                )
            )
            _ok("science_devices.activation_key_hash added")
        else:
            _skip("science_devices.activation_key_hash already exists")

        if not await _column_exists(session, "science_devices", "activation_key_last4"):
            await session.execute(
                text(
                    """
                    ALTER TABLE science_devices
                    ADD COLUMN activation_key_last4 VARCHAR(4) NULL
                    COMMENT '设备激活码后四位'
                    """
                )
            )
            _ok("science_devices.activation_key_last4 added")
        else:
            _skip("science_devices.activation_key_last4 already exists")

        if not await _column_exists(session, "science_devices", "activation_updated_at"):
            await session.execute(
                text(
                    """
                    ALTER TABLE science_devices
                    ADD COLUMN activation_updated_at DATETIME NULL
                    COMMENT '激活码更新时间'
                    """
                )
            )
            _ok("science_devices.activation_updated_at added")
        else:
            _skip("science_devices.activation_updated_at already exists")

        if not await _column_exists(session, "science_devices", "kb_scope_mode"):
            await session.execute(
                text(
                    """
                    ALTER TABLE science_devices
                    ADD COLUMN kb_scope_mode VARCHAR(16) NOT NULL DEFAULT 'dept'
                    COMMENT '设备级知识库范围模式'
                    """
                )
            )
            _ok("science_devices.kb_scope_mode added")
        else:
            _skip("science_devices.kb_scope_mode already exists")

        if not await _column_exists(session, "science_devices", "kb_scope_dept_ids_json"):
            await session.execute(
                text(
                    """
                    ALTER TABLE science_devices
                    ADD COLUMN kb_scope_dept_ids_json JSON NULL
                    COMMENT '设备级部门范围'
                    """
                )
            )
            _ok("science_devices.kb_scope_dept_ids_json added")
        else:
            _skip("science_devices.kb_scope_dept_ids_json already exists")

        if not await _column_exists(session, "science_devices", "kb_scope_file_ids_json"):
            await session.execute(
                text(
                    """
                    ALTER TABLE science_devices
                    ADD COLUMN kb_scope_file_ids_json JSON NULL
                    COMMENT '设备级文件范围'
                    """
                )
            )
            _ok("science_devices.kb_scope_file_ids_json added")
        else:
            _skip("science_devices.kb_scope_file_ids_json already exists")

        if not await _table_exists(session, "science_device_activation_codes"):
            await session.execute(
                text(
                    """
                    CREATE TABLE science_device_activation_codes (
                        id VARCHAR(36) PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
                        name_hint VARCHAR(128) NULL,
                        activation_key_hash VARCHAR(128) NOT NULL,
                        activation_key_last4 VARCHAR(4) NOT NULL,
                        service_user_id VARCHAR(36) NOT NULL,
                        dept_id INT NULL,
                        status VARCHAR(16) NOT NULL DEFAULT 'pending',
                        used_device_id VARCHAR(64) NULL,
                        created_by VARCHAR(36) NULL,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        used_at DATETIME NULL,
                        UNIQUE KEY uq_science_activation_key_hash (activation_key_hash),
                        KEY idx_science_activation_code_workspace_status (workspace_id, status)
                    )
                    """
                )
            )
            _ok("science_device_activation_codes created")
        else:
            _skip("science_device_activation_codes already exists")

        if not await _table_exists(session, "science_sessions"):
            await session.execute(
                text(
                    """
                    CREATE TABLE science_sessions (
                        id VARCHAR(36) PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
                        device_id VARCHAR(64) NOT NULL,
                        visitor_id VARCHAR(64) NULL,
                        status VARCHAR(16) NOT NULL DEFAULT 'active',
                        metadata_json JSON NULL,
                        started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        ended_at DATETIME NULL,
                        KEY idx_science_session_workspace_status (workspace_id, status),
                        KEY idx_science_session_workspace_device (workspace_id, device_id),
                        KEY idx_science_session_visitor (visitor_id)
                    )
                    """
                )
            )
            _ok("science_sessions created")
        else:
            _skip("science_sessions already exists")

        if not await _table_exists(session, "science_wakeword_configs"):
            await session.execute(
                text(
                    """
                    CREATE TABLE science_wakeword_configs (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
                        wakeword VARCHAR(64) NOT NULL DEFAULT '小莎小莎',
                        enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        sensitivity DOUBLE NOT NULL DEFAULT 0.65,
                        updated_by VARCHAR(36) NULL,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uq_science_wakeword_workspace (workspace_id)
                    )
                    """
                )
            )
            _ok("science_wakeword_configs created")
        else:
            _skip("science_wakeword_configs already exists")

        if not await _table_exists(session, "science_voice_profiles"):
            await session.execute(
                text(
                    """
                    CREATE TABLE science_voice_profiles (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
                        child_voice VARCHAR(64) NOT NULL DEFAULT 'child',
                        adult_voice VARCHAR(64) NOT NULL DEFAULT 'female_gentle',
                        elder_voice VARCHAR(64) NOT NULL DEFAULT 'male_broadcast',
                        female_voice VARCHAR(64) NOT NULL DEFAULT 'child',
                        male_voice VARCHAR(64) NOT NULL DEFAULT 'robot',
                        default_voice VARCHAR(64) NOT NULL DEFAULT 'robot',
                        gender_confidence_threshold DOUBLE NOT NULL DEFAULT 0.75,
                        updated_by VARCHAR(36) NULL,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uq_science_voice_profile_workspace (workspace_id)
                    )
                    """
                )
            )
            _ok("science_voice_profiles created")
        else:
            _skip("science_voice_profiles already exists")

        if not await _column_exists(session, "science_voice_profiles", "child_voice"):
            await session.execute(
                text(
                    """
                    ALTER TABLE science_voice_profiles
                    ADD COLUMN child_voice VARCHAR(64) NOT NULL DEFAULT 'child'
                    COMMENT '儿童音色'
                    """
                )
            )
            await session.execute(
                text(
                    """
                    UPDATE science_voice_profiles
                    SET child_voice = COALESCE(NULLIF(female_voice, ''), 'child')
                    WHERE child_voice IS NULL OR child_voice = ''
                    """
                )
            )
            _ok("science_voice_profiles.child_voice added")
        else:
            _skip("science_voice_profiles.child_voice already exists")

        if not await _column_exists(session, "science_voice_profiles", "adult_voice"):
            await session.execute(
                text(
                    """
                    ALTER TABLE science_voice_profiles
                    ADD COLUMN adult_voice VARCHAR(64) NOT NULL DEFAULT 'female_gentle'
                    COMMENT '成人音色'
                    """
                )
            )
            await session.execute(
                text(
                    """
                    UPDATE science_voice_profiles
                    SET adult_voice = COALESCE(NULLIF(female_voice, ''), NULLIF(default_voice, ''), 'female_gentle')
                    WHERE adult_voice IS NULL OR adult_voice = ''
                    """
                )
            )
            _ok("science_voice_profiles.adult_voice added")
        else:
            _skip("science_voice_profiles.adult_voice already exists")

        if not await _column_exists(session, "science_voice_profiles", "elder_voice"):
            await session.execute(
                text(
                    """
                    ALTER TABLE science_voice_profiles
                    ADD COLUMN elder_voice VARCHAR(64) NOT NULL DEFAULT 'male_broadcast'
                    COMMENT '长者音色'
                    """
                )
            )
            await session.execute(
                text(
                    """
                    UPDATE science_voice_profiles
                    SET elder_voice = COALESCE(NULLIF(male_voice, ''), NULLIF(default_voice, ''), 'male_broadcast')
                    WHERE elder_voice IS NULL OR elder_voice = ''
                    """
                )
            )
            _ok("science_voice_profiles.elder_voice added")
        else:
            _skip("science_voice_profiles.elder_voice already exists")

        if not await _table_exists(session, "science_workspace_settings"):
            await session.execute(
                text(
                    """
                    CREATE TABLE science_workspace_settings (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
                        doc_scope_json JSON NULL,
                        updated_by VARCHAR(36) NULL,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uq_science_workspace_settings_workspace (workspace_id)
                    )
                    """
                )
            )
            _ok("science_workspace_settings created")
        else:
            _skip("science_workspace_settings already exists")

        if not await _table_exists(session, "science_quiz_questions"):
            await session.execute(
                text(
                    """
                    CREATE TABLE science_quiz_questions (
                        id VARCHAR(36) PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
                        question_text TEXT NOT NULL,
                        options_json JSON NOT NULL,
                        answer_key VARCHAR(255) NOT NULL,
                        explanation TEXT NULL,
                        difficulty INT NOT NULL DEFAULT 1,
                        source_type VARCHAR(16) NOT NULL DEFAULT 'bank',
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        created_by VARCHAR(36) NULL,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        KEY idx_science_quiz_workspace_active (workspace_id, is_active),
                        KEY idx_science_quiz_workspace_source (workspace_id, source_type)
                    )
                    """
                )
            )
            _ok("science_quiz_questions created")
        else:
            _skip("science_quiz_questions already exists")

        if not await _table_exists(session, "science_quiz_attempts"):
            await session.execute(
                text(
                    """
                    CREATE TABLE science_quiz_attempts (
                        id VARCHAR(36) PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
                        session_id VARCHAR(36) NOT NULL,
                        device_id VARCHAR(64) NOT NULL,
                        question_id VARCHAR(36) NOT NULL,
                        user_answer VARCHAR(255) NOT NULL,
                        is_correct BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        KEY idx_science_attempt_workspace_session (workspace_id, session_id),
                        KEY idx_science_attempt_workspace_correct (workspace_id, is_correct),
                        KEY idx_science_attempt_question (question_id)
                    )
                    """
                )
            )
            _ok("science_quiz_attempts created")
        else:
            _skip("science_quiz_attempts already exists")

        if not await _table_exists(session, "science_reward_policies"):
            await session.execute(
                text(
                    """
                    CREATE TABLE science_reward_policies (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
                        required_correct_count INT NOT NULL DEFAULT 3,
                        period_hours INT NOT NULL DEFAULT 24,
                        coupon_id VARCHAR(36) NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        updated_by VARCHAR(36) NULL,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uq_science_reward_policy_workspace (workspace_id),
                        KEY idx_science_reward_workspace_active (workspace_id, is_active)
                    )
                    """
                )
            )
            _ok("science_reward_policies created")
        else:
            _skip("science_reward_policies already exists")

        if not await _table_exists(session, "science_coupons"):
            await session.execute(
                text(
                    """
                    CREATE TABLE science_coupons (
                        id VARCHAR(36) PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
                        title VARCHAR(128) NOT NULL,
                        description TEXT NULL,
                        merchant_name VARCHAR(128) NULL,
                        redeem_link VARCHAR(512) NULL,
                        status VARCHAR(16) NOT NULL DEFAULT 'active',
                        total_stock INT NOT NULL DEFAULT 0,
                        issued_count INT NOT NULL DEFAULT 0,
                        redeemed_count INT NOT NULL DEFAULT 0,
                        expires_at DATETIME NULL,
                        created_by VARCHAR(36) NULL,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        KEY idx_science_coupon_workspace_status (workspace_id, status)
                    )
                    """
                )
            )
            _ok("science_coupons created")
        else:
            _skip("science_coupons already exists")

        if await _table_exists(session, "science_coupons"):
            if not await _column_exists(session, "science_coupons", "redeem_link"):
                await session.execute(
                    text(
                        """
                        ALTER TABLE science_coupons
                        ADD COLUMN redeem_link VARCHAR(512) NULL
                        COMMENT '优惠券跳转链接'
                        AFTER merchant_name
                        """
                    )
                )
                _ok("science_coupons.redeem_link added")
            else:
                _skip("science_coupons.redeem_link already exists")

        if not await _table_exists(session, "science_coupon_issuances"):
            await session.execute(
                text(
                    """
                    CREATE TABLE science_coupon_issuances (
                        id VARCHAR(36) PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
                        coupon_id VARCHAR(36) NOT NULL,
                        session_id VARCHAR(36) NOT NULL,
                        device_id VARCHAR(64) NOT NULL,
                        coupon_code VARCHAR(64) NOT NULL,
                        status VARCHAR(16) NOT NULL DEFAULT 'issued',
                        issued_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        expires_at DATETIME NULL,
                        redeemed_at DATETIME NULL,
                        UNIQUE KEY uq_science_coupon_code (coupon_code),
                        KEY idx_science_issuance_workspace_session (workspace_id, session_id),
                        KEY idx_science_issuance_workspace_status (workspace_id, status)
                    )
                    """
                )
            )
            _ok("science_coupon_issuances created")
        else:
            _skip("science_coupon_issuances already exists")

        if not await _table_exists(session, "science_coupon_redemptions"):
            await session.execute(
                text(
                    """
                    CREATE TABLE science_coupon_redemptions (
                        id VARCHAR(36) PRIMARY KEY,
                        workspace_id VARCHAR(36) NOT NULL DEFAULT 'default',
                        issuance_id VARCHAR(36) NOT NULL,
                        redeemed_by VARCHAR(36) NULL,
                        note TEXT NULL,
                        redeemed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE KEY uq_science_redeem_issuance (issuance_id),
                        KEY idx_science_redeem_workspace (workspace_id),
                        KEY idx_science_redeem_issuance_redeemed (issuance_id, redeemed_at)
                    )
                    """
                )
            )
            _ok("science_coupon_redemptions created")
        else:
            _skip("science_coupon_redemptions already exists")

        # Existing table but missing redemption unique/index in some envs
        if await _table_exists(session, "science_coupon_redemptions"):
            if not await _constraint_exists(
                session, "science_coupon_redemptions", "uq_science_redeem_issuance"
            ):
                await session.execute(
                    text(
                        """
                        ALTER TABLE science_coupon_redemptions
                        ADD CONSTRAINT uq_science_redeem_issuance UNIQUE (issuance_id)
                        """
                    )
                )
                _ok("uq_science_redeem_issuance added")
            else:
                _skip("uq_science_redeem_issuance already exists")

            if not await _index_exists(
                session,
                "science_coupon_redemptions",
                "idx_science_redeem_issuance_redeemed",
            ):
                await session.execute(
                    text(
                        """
                        CREATE INDEX idx_science_redeem_issuance_redeemed
                        ON science_coupon_redemptions(issuance_id, redeemed_at)
                        """
                    )
                )
                _ok("idx_science_redeem_issuance_redeemed created")
            else:
                _skip("idx_science_redeem_issuance_redeemed already exists")

    print("=" * 72)
    print("Science experience migration complete")
    print("=" * 72)


if __name__ == "__main__":
    import platform

    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_migration())
