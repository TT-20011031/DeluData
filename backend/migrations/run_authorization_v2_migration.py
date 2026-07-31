"""Offline authorization-v2 migration with dry-run, verification and rollback modes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import bindparam, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.db.database import get_async_db_manager  # noqa: E402
from app.core.security.capabilities import (  # noqa: E402
    AUTHORIZATION_V2_MIGRATION_ID,
    CAPABILITIES,
)


MIGRATION_ID = AUTHORIZATION_V2_MIGRATION_ID


def statements(sql: str) -> list[str]:
    without_comments = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [item.strip() for item in without_comments.split(";") if item.strip()]


async def table_collation(session, table_name: str) -> str | None:
    collation = (await session.execute(text("""
        SELECT table_collation FROM information_schema.tables
        WHERE table_schema=DATABASE() AND table_name=:table_name
    """), {"table_name": table_name})).scalar_one_or_none()
    if not collation:
        return None
    value = str(collation)
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise RuntimeError(f"unsafe database collation: {value!r}")
    return value


async def migration_collations(session) -> dict[str, str]:
    """Match each V2 domain to its legacy tables to keep cross-version joins safe."""
    default_collation = str((await session.execute(text("""
        SELECT default_collation_name FROM information_schema.schemata
        WHERE schema_name=DATABASE()
    """))).scalar_one())
    if not re.fullmatch(r"[A-Za-z0-9_]+", default_collation):
        raise RuntimeError(f"unsafe database collation: {default_collation!r}")
    auth = await table_collation(session, "sys_users") or default_collation
    semantic = await table_collation(session, "semantic_access_policies") or auth
    return {"authorization": auth, "semantic": semantic}


async def counts(session) -> dict[str, int]:
    result = {}
    for name in ("sys_users", "sys_departments", "sys_roles", "sys_user_roles", "semantic_access_policies"):
        try:
            result[name] = int((await session.execute(text(f"SELECT COUNT(*) FROM {name}"))).scalar_one())
        except Exception:
            result[name] = -1
    return result


async def backfill(session) -> None:
    for code, (module, description) in CAPABILITIES.items():
        await session.execute(text("""
            INSERT INTO sys_permissions (code, module, description)
            VALUES (:code, :module, :description)
            ON DUPLICATE KEY UPDATE module=VALUES(module), description=VALUES(description)
        """), {"code": code, "module": module, "description": description})
    canonical_codes = sorted(CAPABILITIES)
    await session.execute(text("""
        INSERT IGNORE INTO authorization_migration_wildcard_roles (role_id)
        SELECT DISTINCT role.id FROM sys_roles role
        JOIN sys_role_permissions rp ON rp.role_id=role.id
        JOIN sys_permissions permission ON permission.id=rp.permission_id
        WHERE role.workspace_id IS NOT NULL AND permission.code='*'
    """))
    # Expand wildcard roles into the canonical catalogue before comparison.
    # Global templates keep their wildcard for old-release rollback; workspace
    # roles have it removed below so the V2 runtime never receives implicit '*'.
    await session.execute(text("""
        INSERT IGNORE INTO sys_role_permissions (role_id, permission_id)
        SELECT DISTINCT role.id, canonical_permission.id
        FROM sys_roles role
        JOIN sys_role_permissions wildcard_rp ON wildcard_rp.role_id=role.id
        JOIN sys_permissions wildcard_permission
          ON wildcard_permission.id=wildcard_rp.permission_id AND wildcard_permission.code='*'
        JOIN sys_permissions canonical_permission
          ON canonical_permission.code IN :canonical_codes
    """).bindparams(bindparam("canonical_codes", expanding=True)), {
        "canonical_codes": canonical_codes,
    })
    await session.execute(text("""
        INSERT IGNORE INTO sys_role_permissions (role_id, permission_id)
        SELECT wildcard.role_id, permission.id
        FROM authorization_migration_wildcard_roles wildcard
        JOIN sys_permissions permission ON permission.code IN :canonical_codes
    """).bindparams(bindparam("canonical_codes", expanding=True)), {
        "canonical_codes": canonical_codes,
    })
    await session.execute(text("""
        DELETE rp FROM sys_role_permissions rp
        JOIN authorization_migration_wildcard_roles wildcard ON wildcard.role_id=rp.role_id
        JOIN sys_permissions permission ON permission.id=rp.permission_id
        WHERE permission.code='*'
    """))
    await session.execute(text("""
        INSERT IGNORE INTO sys_authorization_revisions (workspace_id, revision, updated_at)
        SELECT DISTINCT workspace_id, 1, CURRENT_TIMESTAMP FROM sys_users
    """))
    await session.execute(text("""
        INSERT IGNORE INTO authorization_migration_user_backup
          (user_id, workspace_id, original_department_id)
        SELECT id, workspace_id, department_id FROM sys_users
    """))
    # Create a deterministic holding organization for users without a department.
    await session.execute(text("""
        INSERT INTO sys_departments (workspace_id, parent_id, name, code, ancestors, order_num, status)
        SELECT workspaces.workspace_id, NULL, '待归属组织', '__UNASSIGNED__', '/', 999999, 1
        FROM (SELECT DISTINCT workspace_id FROM sys_users) workspaces
        WHERE NOT EXISTS (
          SELECT 1 FROM sys_departments d WHERE d.workspace_id = workspaces.workspace_id AND d.code = '__UNASSIGNED__'
        )
    """))
    await session.execute(text("""
        UPDATE sys_users u JOIN sys_departments d
          ON d.workspace_id = u.workspace_id AND d.code = '__UNASSIGNED__'
        SET u.department_id = d.id WHERE u.department_id IS NULL
    """))

    # Workspace-owned copies of immutable global role templates.  A name
    # collision is blocked before any permissions are copied, so a tenant role
    # can never gain authority merely because it is named like a template.
    template_collision_count = int((await session.execute(text("""
        SELECT COUNT(*) FROM sys_roles local_role
        JOIN sys_roles global_role ON global_role.workspace_id IS NULL
        WHERE local_role.workspace_id IS NOT NULL
          AND local_role.name=CONCAT('系统模板-', global_role.name)
          AND NOT EXISTS (
            SELECT 1 FROM authorization_migration_role_map role_map
            WHERE role_map.local_role_id=local_role.id
          )
    """))).scalar_one())
    if template_collision_count:
        raise RuntimeError(f"system template role name collision: {template_collision_count}")

    # Workspace-owned copies of immutable global role templates.
    await session.execute(text("""
        INSERT INTO sys_roles (name, description, workspace_id, is_system, data_scope)
        SELECT CONCAT('系统模板-', g.name), g.description, w.workspace_id, 0, g.data_scope
        FROM sys_roles g CROSS JOIN (SELECT DISTINCT workspace_id FROM sys_users) w
        WHERE g.workspace_id IS NULL AND NOT EXISTS (
          SELECT 1 FROM sys_roles local_role
          WHERE local_role.workspace_id = w.workspace_id AND local_role.name = CONCAT('系统模板-', g.name)
        )
    """))
    await session.execute(text("""
        INSERT IGNORE INTO authorization_migration_role_map
          (global_role_id, workspace_id, local_role_id)
        SELECT global_role.id, local_role.workspace_id, local_role.id
        FROM sys_roles global_role JOIN sys_roles local_role
          ON local_role.workspace_id IS NOT NULL
         AND local_role.name=CONCAT('系统模板-', global_role.name)
        WHERE global_role.workspace_id IS NULL
    """))
    await session.execute(text("""
        INSERT IGNORE INTO sys_role_permissions (role_id, permission_id)
        SELECT local_role.id, rp.permission_id FROM sys_roles global_role
        JOIN sys_role_permissions rp ON rp.role_id = global_role.id
        JOIN sys_permissions permission ON permission.id = rp.permission_id AND permission.code <> '*'
        JOIN sys_roles local_role ON local_role.name = CONCAT('系统模板-', global_role.name) AND local_role.workspace_id IS NOT NULL
        WHERE global_role.workspace_id IS NULL
    """))
    await session.execute(text("""
        INSERT IGNORE INTO sys_role_permissions (role_id, permission_id)
        SELECT local_role.id, permission.id
        FROM sys_roles global_role
        JOIN sys_role_permissions wildcard_rp ON wildcard_rp.role_id=global_role.id
        JOIN sys_permissions wildcard_permission
          ON wildcard_permission.id=wildcard_rp.permission_id AND wildcard_permission.code='*'
        JOIN sys_roles local_role
          ON local_role.name=CONCAT('系统模板-', global_role.name)
         AND local_role.workspace_id IS NOT NULL
        JOIN sys_permissions permission ON permission.code IN :canonical_codes
        WHERE global_role.workspace_id IS NULL
    """).bindparams(bindparam("canonical_codes", expanding=True)), {
        "canonical_codes": canonical_codes,
    })

    await session.execute(text("DROP TEMPORARY TABLE IF EXISTS tmp_auth_user_signatures"))
    await session.execute(text("""
        CREATE TEMPORARY TABLE tmp_auth_user_signatures AS
        SELECT u.id user_id, u.workspace_id, u.department_id,
               COALESCE(GROUP_CONCAT(CONCAT(COALESCE(r.workspace_id,'GLOBAL'), ':', r.id) ORDER BY r.id SEPARATOR '|'), '__NO_ROLE__') role_signature
        FROM sys_users u LEFT JOIN sys_user_roles ur ON ur.user_id = u.id
        LEFT JOIN sys_roles r ON r.id = ur.role_id
        GROUP BY u.id, u.workspace_id, u.department_id
    """))
    await session.execute(text("""
        INSERT IGNORE INTO sys_positions (workspace_id, org_unit_id, name, code, status, legacy_generated)
        SELECT DISTINCT workspace_id, department_id,
          CONCAT('迁移岗位-', LEFT(MD5(role_signature), 8)), CONCAT('legacy-', MD5(role_signature)), 1, 1
        FROM tmp_auth_user_signatures
    """))
    await session.execute(text("""
        INSERT INTO sys_assignments (workspace_id, user_id, position_id, is_primary, starts_at, status)
        SELECT s.workspace_id, s.user_id, p.id, 1, NOW(), 1
        FROM tmp_auth_user_signatures s JOIN sys_positions p
          ON p.workspace_id = s.workspace_id AND p.org_unit_id = s.department_id
         AND BINARY p.code = BINARY CONCAT('legacy-', MD5(s.role_signature))
        WHERE NOT EXISTS (SELECT 1 FROM sys_assignments a WHERE a.workspace_id=s.workspace_id AND a.user_id=s.user_id)
    """))
    await session.execute(text("""
        INSERT INTO sys_role_bindings (
          workspace_id, position_id, role_id, scope_type, scope_org_unit_id,
          custom_org_unit_ids, starts_at, status, created_by
        )
        SELECT DISTINCT s.workspace_id, p.id, local_role.id,
          CASE COALESCE(old_role.data_scope,4) WHEN 1 THEN 'workspace' WHEN 2 THEN 'org_tree' WHEN 3 THEN 'org_unit' ELSE 'self' END,
          s.department_id, JSON_ARRAY(), NOW(), 1, s.user_id
        FROM tmp_auth_user_signatures s
        JOIN sys_positions p ON p.workspace_id=s.workspace_id AND p.org_unit_id=s.department_id
          AND BINARY p.code=BINARY CONCAT('legacy-',MD5(s.role_signature))
        JOIN sys_user_roles ur ON ur.user_id=s.user_id JOIN sys_roles old_role ON old_role.id=ur.role_id
        JOIN sys_roles local_role ON local_role.workspace_id=s.workspace_id AND (
          (old_role.workspace_id=s.workspace_id AND local_role.id=old_role.id)
          OR (old_role.workspace_id IS NULL AND local_role.name=CONCAT('系统模板-', old_role.name))
        )
        WHERE NOT EXISTS (
          SELECT 1 FROM sys_role_bindings b WHERE b.workspace_id=s.workspace_id AND b.position_id=p.id AND b.role_id=local_role.id
        )
    """))

    owner_role_collision = int((await session.execute(text("""
        SELECT COUNT(*) FROM sys_roles
        WHERE workspace_id IS NOT NULL AND name='工作区所有者'
          AND description<>'authorization-v2 system-managed workspace owner'
    """))).scalar_one())
    if owner_role_collision:
        raise RuntimeError(f"workspace owner role name collision: {owner_role_collision}")
    await session.execute(text("""
        INSERT INTO sys_roles (name, description, workspace_id, is_system, data_scope)
        SELECT '工作区所有者', 'authorization-v2 system-managed workspace owner',
               workspace.id, 1, 4
        FROM sys_workspaces workspace
        WHERE workspace.owner_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM sys_roles role
          WHERE role.workspace_id=workspace.id AND role.name='工作区所有者'
        )
    """))
    await session.execute(text("""
        INSERT IGNORE INTO sys_role_permissions (role_id, permission_id)
        SELECT role.id, permission.id FROM sys_roles role
        JOIN sys_permissions permission ON permission.code IN :canonical_codes
        WHERE role.description='authorization-v2 system-managed workspace owner'
    """).bindparams(bindparam("canonical_codes", expanding=True)), {
        "canonical_codes": canonical_codes,
    })
    await session.execute(text("""
        INSERT IGNORE INTO sys_positions
          (workspace_id, org_unit_id, name, code, status, legacy_generated)
        SELECT workspace.id, user.department_id, '工作区所有者', 'WORKSPACE_OWNER', 1, 1
        FROM sys_workspaces workspace JOIN sys_users user ON user.id=workspace.owner_id
        WHERE workspace.owner_id IS NOT NULL
    """))
    await session.execute(text("""
        INSERT INTO sys_assignments
          (workspace_id, user_id, position_id, is_primary, starts_at, status)
        SELECT workspace.id, workspace.owner_id, position.id, 0, NOW(), 1
        FROM sys_workspaces workspace
        JOIN sys_positions position
          ON position.workspace_id=workspace.id AND position.code='WORKSPACE_OWNER'
        WHERE workspace.owner_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM sys_assignments assignment
          WHERE assignment.workspace_id=workspace.id
            AND assignment.user_id=workspace.owner_id
            AND assignment.position_id=position.id
        )
    """))
    await session.execute(text("""
        INSERT INTO sys_role_bindings
          (workspace_id, position_id, role_id, scope_type, custom_org_unit_ids,
           starts_at, status, created_by)
        SELECT workspace.id, position.id, role.id, 'workspace', JSON_ARRAY(),
               NOW(), 1, workspace.owner_id
        FROM sys_workspaces workspace
        JOIN sys_positions position
          ON position.workspace_id=workspace.id AND position.code='WORKSPACE_OWNER'
        JOIN sys_roles role
          ON role.workspace_id=workspace.id
         AND role.description='authorization-v2 system-managed workspace owner'
        WHERE workspace.owner_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM sys_role_bindings binding
          WHERE binding.workspace_id=workspace.id
            AND binding.position_id=position.id AND binding.role_id=role.id
        )
    """))

    # Legacy per-user table grants are intentionally archived, not activated.
    await session.execute(text("""
        INSERT IGNORE INTO sys_user_table_permissions_archive
          (source_id, user_id, table_name, archived_reason)
        SELECT id, user_id, table_name, 'authorization-v2: runtime table grants retired'
        FROM sys_user_table_permissions
    """))


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


async def _record_review(session, workspace_id: str, item_type: str, source_id: str, reason: str, payload: dict) -> None:
    await session.execute(text("""
        INSERT INTO authorization_migration_review_items
          (workspace_id, item_type, source_id, reason, payload_json)
        VALUES (:workspace_id, :item_type, :source_id, :reason, CAST(:payload AS JSON))
        ON DUPLICATE KEY UPDATE reason=VALUES(reason), payload_json=VALUES(payload_json)
    """), {
        "workspace_id": workspace_id,
        "item_type": item_type,
        "source_id": source_id,
        "reason": reason,
        "payload": json.dumps(payload, ensure_ascii=False),
    })


async def _role_binding_org_ids(session, binding_id: int) -> list[int] | None:
    row = (await session.execute(text("""
        SELECT b.workspace_id, b.scope_type, COALESCE(b.scope_org_unit_id, p.org_unit_id) anchor_id,
               b.custom_org_unit_ids
        FROM sys_role_bindings b JOIN sys_positions p ON p.id=b.position_id
        WHERE b.id=:binding_id
    """), {"binding_id": binding_id})).mappings().one()
    scope_type = row["scope_type"]
    if scope_type == "workspace":
        result = await session.execute(text("""
            SELECT id FROM sys_departments
            WHERE workspace_id=:workspace_id AND status=1 ORDER BY id
        """), {"workspace_id": row["workspace_id"]})
        return [int(item) for item in result.scalars().all()]
    if scope_type in {"self", "org_unit"}:
        return [int(row["anchor_id"])]
    if scope_type == "custom":
        return sorted({int(item) for item in _json(row["custom_org_unit_ids"], [])})
    result = await session.execute(text("""
        SELECT id FROM sys_departments
        WHERE workspace_id=:workspace_id AND status=1
          AND (id=:anchor_id OR ancestors LIKE :descendant_pattern)
        ORDER BY id
    """), {
        "workspace_id": row["workspace_id"],
        "anchor_id": int(row["anchor_id"]),
        "descendant_pattern": f"%/{int(row['anchor_id'])}/%",
    })
    return [int(item) for item in result.scalars().all()]


async def _copy_policy_effects(
    session,
    *,
    workspace_id: str,
    datasource_id: int,
    binding_id: int,
    version_id: int,
    policy_ids: list[int],
    role_binding_id: int | None,
) -> None:
    rows = (await session.execute(text("""
        SELECT policy_id, effect_key, asset_type, asset_id, effect_type,
               condition_json, compiled_sql, priority
        FROM semantic_access_policy_effects
        WHERE enabled=1 AND policy_id IN :policy_ids
        ORDER BY policy_id, priority DESC, id
    """).bindparams(bindparam("policy_ids", expanding=True)), {"policy_ids": policy_ids})).mappings().all()

    org_ids = await _role_binding_org_ids(session, role_binding_id) if role_binding_id else None
    restrict_to_org = role_binding_id is not None
    row_filters: dict[tuple[int, int], list[dict]] = {}
    visible_tables: set[tuple[int, int]] = set()
    copied: list[dict] = []
    for row in rows:
        condition = _json(row["condition_json"], {})
        key = (int(row["policy_id"]), int(row["asset_id"]))
        if restrict_to_org and row["asset_type"] == "table" and row["effect_type"] == "row_filter":
            row_filters.setdefault(key, []).append(condition)
            continue
        if restrict_to_org and row["asset_type"] == "table" and row["effect_type"] == "visible":
            visible_tables.add(key)
            condition = {**condition, "row_scope": {"type": "authorization"}}
        copied.append({
            "workspace_id": workspace_id,
            "datasource_id": datasource_id,
            "binding_id": binding_id,
            "version_id": version_id,
            "effect_key": f"legacy:{row['policy_id']}:{row['effect_key']}"[:128],
            "asset_type": row["asset_type"],
            "asset_id": int(row["asset_id"]),
            "effect_type": row["effect_type"],
            "condition_json": json.dumps(condition, ensure_ascii=False),
            "compiled_sql": row["compiled_sql"],
            "priority": int(row["priority"] or 100),
        })

    if restrict_to_org:
        for policy_id, table_id in sorted(visible_tables):
            mapping = (await session.execute(text("""
                SELECT org_column_id, org_value_kind FROM semantic_ownership_mappings
                WHERE workspace_id=:workspace_id AND datasource_id=:datasource_id AND table_id=:table_id
            """), {
                "workspace_id": workspace_id,
                "datasource_id": datasource_id,
                "table_id": table_id,
            })).mappings().one_or_none()
            if mapping is None:
                await _record_review(
                    session,
                    workspace_id,
                    "ownership_mapping_missing",
                    f"{binding_id}:{table_id}",
                    "组织范围授权缺少语义表组织归属字段，迁移必须阻断",
                    {"role_binding_id": binding_id, "table_id": table_id},
                )
                continue
            org_values = org_ids
            if mapping["org_value_kind"] == "code":
                org_values = list((await session.execute(text("""
                    SELECT code FROM sys_departments
                    WHERE workspace_id=:workspace_id AND id IN :org_ids
                """).bindparams(bindparam("org_ids", expanding=True)), {
                    "workspace_id": workspace_id,
                    "org_ids": org_ids,
                })).scalars().all())
            auth_condition = {
                "_authorization_scope": True,
                "column_id": int(mapping["org_column_id"]),
                "operator": "in",
                "value": org_values,
            }
            source_filters = row_filters.get((policy_id, table_id)) or [None]
            for index, source_condition in enumerate(source_filters):
                condition = auth_condition if source_condition is None else {
                    "op": "AND",
                    "rules": [auth_condition, source_condition],
                }
                copied.append({
                    "workspace_id": workspace_id,
                    "datasource_id": datasource_id,
                    "binding_id": binding_id,
                    "version_id": version_id,
                    "effect_key": f"legacy:{policy_id}:auth:{table_id}:{index}"[:128],
                    "asset_type": "table",
                    "asset_id": table_id,
                    "effect_type": "row_filter",
                    "condition_json": json.dumps(condition, ensure_ascii=False),
                    "compiled_sql": None,
                    "priority": 100,
                })

    if copied:
        await session.execute(text("""
            INSERT IGNORE INTO semantic_policy_version_effects
              (workspace_id, datasource_id, binding_id, version_id, effect_key,
               asset_type, asset_id, effect_type, condition_json, compiled_sql, priority)
            VALUES
              (:workspace_id, :datasource_id, :binding_id, :version_id, :effect_key,
               :asset_type, :asset_id, :effect_type, CAST(:condition_json AS JSON), :compiled_sql, :priority)
        """), copied)


async def migrate_semantic_policies(session) -> None:
    policies = (await session.execute(text("""
        SELECT id, workspace_id, datasource_id, name, source_text, subject_json,
               asset_selector_json, validation_json, compile_summary_json,
               schema_fingerprint, created_by
        FROM semantic_access_policies
        WHERE status='active'
        ORDER BY workspace_id, datasource_id, id
    """))).mappings().all()
    grouped: dict[tuple[str, int, str, str], list[dict]] = {}
    now = datetime.utcnow()
    for raw in policies:
        policy = dict(raw)
        subject = _json(policy["subject_json"], {})
        subject_type = str(subject.get("type") or "")
        subject_id = str(subject.get("id") or "*")
        targets: list[tuple[str, str]] = []
        if subject_type == "all":
            targets = [("baseline", "*")]
        elif subject_type == "role":
            role_row = (await session.execute(text("SELECT name, workspace_id FROM sys_roles WHERE id=:role_id"), {"role_id": subject_id})).mappings().one_or_none()
            if role_row:
                role_name = f"系统模板-{role_row['name']}" if role_row["workspace_id"] is None else role_row["name"]
                binding_ids = (await session.execute(text("""
                    SELECT b.id FROM sys_role_bindings b
                    JOIN sys_roles r ON r.id=b.role_id
                    WHERE b.workspace_id=:workspace_id AND r.name=:role_name
                """), {"workspace_id": policy["workspace_id"], "role_name": role_name})).scalars().all()
                targets = [("role_binding", str(item)) for item in binding_ids]
        elif subject_type == "user":
            user_exists = (await session.execute(text("""
                SELECT id FROM sys_users WHERE id=:user_id AND workspace_id=:workspace_id
            """), {"user_id": subject_id, "workspace_id": policy["workspace_id"]})).scalar_one_or_none()
            if user_exists:
                reason = f"authorization-v2 migrated semantic policy #{policy['id']}"
                exception_id = (await session.execute(text("""
                    SELECT id FROM sys_authorization_exceptions
                    WHERE workspace_id=:workspace_id AND user_id=:user_id AND reason=:reason
                """), {"workspace_id": policy["workspace_id"], "user_id": subject_id, "reason": reason})).scalar_one_or_none()
                if exception_id is None:
                    owner_id = policy["created_by"] or subject_id
                    owner_exists = (await session.execute(text("""
                        SELECT id FROM sys_users WHERE id=:owner_id AND workspace_id=:workspace_id
                    """), {"owner_id": owner_id, "workspace_id": policy["workspace_id"]})).scalar_one_or_none()
                    if owner_exists is None:
                        owner_id = subject_id
                    result = await session.execute(text("""
                        INSERT INTO sys_authorization_exceptions
                          (workspace_id, user_id, effect_type, capability_codes, scope_type,
                           scope_org_unit_ids, reason, owner_id, starts_at, ends_at, status, created_by)
                        VALUES (:workspace_id, :user_id, 'allow', JSON_ARRAY(), 'workspace',
                                JSON_ARRAY(), :reason, :owner_id, :starts_at, :ends_at, 1, :owner_id)
                    """), {
                        "workspace_id": policy["workspace_id"],
                        "user_id": subject_id,
                        "reason": reason,
                        "owner_id": owner_id,
                        "starts_at": now,
                        "ends_at": now + timedelta(days=90),
                    })
                    exception_id = int(result.lastrowid)
                targets = [("user_exception", str(exception_id))]
                await _record_review(
                    session,
                    policy["workspace_id"],
                    "migrated_user_semantic_exception",
                    str(policy["id"]),
                    "迁移产生的用户例外最长保留 90 天，必须人工复核",
                    {"policy_id": policy["id"], "exception_id": exception_id, "user_id": subject_id},
                )

        if not targets:
            await _record_review(
                session,
                policy["workspace_id"],
                "semantic_policy_unmapped",
                str(policy["id"]),
                "有效旧策略没有可解释的新授权目标",
                {"policy_id": policy["id"], "subject": subject},
            )
            continue
        for target_type, target_id in targets:
            grouped.setdefault((policy["workspace_id"], int(policy["datasource_id"]), target_type, target_id), []).append(policy)
            await session.execute(text("""
                INSERT IGNORE INTO authorization_migration_source_map
                  (workspace_id, source_type, source_id, target_type, target_id)
                VALUES (:workspace_id, 'semantic_access_policy', :source_id, :target_type, :target_id)
            """), {
                "workspace_id": policy["workspace_id"],
                "source_id": str(policy["id"]),
                "target_type": target_type,
                "target_id": target_id,
            })

    for (workspace_id, datasource_id, target_type, target_id), source_policies in grouped.items():
        await session.execute(text("""
            INSERT IGNORE INTO semantic_policy_bindings
              (workspace_id, datasource_id, target_type, target_id, revision, status, created_by)
            VALUES (:workspace_id, :datasource_id, :target_type, :target_id, 0, 1, 'migration:v2')
        """), locals())
        binding = (await session.execute(text("""
            SELECT id, active_version_id FROM semantic_policy_bindings
            WHERE workspace_id=:workspace_id AND datasource_id=:datasource_id
              AND target_type=:target_type AND target_id=:target_id
        """), locals())).mappings().one()
        if binding["active_version_id"] is not None:
            continue
        table_rules: dict[int, dict] = {}
        for policy in source_policies:
            for rule in _json(policy["asset_selector_json"], {}).get("tables", []):
                if isinstance(rule, dict) and rule.get("table_id") is not None:
                    table_rules[int(rule["table_id"])] = rule
        definition = {
            "name": "迁移权限策略",
            "tables": list(table_rules.values()),
            "legacy_policy_ids": [int(item["id"]) for item in source_policies],
        }
        last_policy = source_policies[-1]
        result = await session.execute(text("""
            INSERT INTO semantic_policy_versions
              (workspace_id, datasource_id, binding_id, version, definition_json, source_text,
               validation_json, compile_summary_json, schema_fingerprint, created_by)
            VALUES (:workspace_id, :datasource_id, :binding_id, 1, CAST(:definition AS JSON),
                    :source_text, CAST(:validation AS JSON), CAST(:summary AS JSON),
                    :schema_fingerprint, 'migration:v2')
        """), {
            "workspace_id": workspace_id,
            "datasource_id": datasource_id,
            "binding_id": int(binding["id"]),
            "definition": json.dumps(definition, ensure_ascii=False),
            "source_text": last_policy["source_text"],
            "validation": json.dumps({"blockers": [], "warnings": [{"code": "legacy_import", "message": "由旧策略迁移"}]}, ensure_ascii=False),
            "summary": json.dumps({"legacy_policy_ids": definition["legacy_policy_ids"]}),
            "schema_fingerprint": last_policy["schema_fingerprint"],
        })
        version_id = int(result.lastrowid)
        await _copy_policy_effects(
            session,
            workspace_id=workspace_id,
            datasource_id=datasource_id,
            binding_id=int(binding["id"]),
            version_id=version_id,
            policy_ids=definition["legacy_policy_ids"],
            role_binding_id=int(target_id) if target_type == "role_binding" else None,
        )
        await session.execute(text("""
            UPDATE semantic_policy_bindings SET active_version_id=:version_id, revision=1
            WHERE id=:binding_id
        """), {"version_id": version_id, "binding_id": int(binding["id"])})


async def seed_ownership_mappings(session, file_path: str) -> None:
    rows = json.loads(Path(file_path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("ownership mapping file must contain a JSON array")
    for item in rows:
        required = {"workspace_id", "datasource_id", "table_id", "org_column_id"}
        if not isinstance(item, dict) or not required.issubset(item):
            raise ValueError(f"invalid ownership mapping: {item!r}")
        valid = (await session.execute(text("""
            SELECT c.id FROM semantic_columns c JOIN semantic_tables t ON t.id=c.table_id
            WHERE c.id=:org_column_id AND c.table_id=:table_id
              AND c.workspace_id=:workspace_id AND c.datasource_id=:datasource_id
              AND t.workspace_id=:workspace_id AND t.datasource_id=:datasource_id
        """), item)).scalar_one_or_none()
        if valid is None:
            raise ValueError(f"ownership mapping crosses datasource or references a missing column: {item}")
        await session.execute(text("""
            INSERT INTO semantic_ownership_mappings
              (workspace_id, datasource_id, table_id, org_column_id, org_value_kind,
               user_column_id, user_value_kind, updated_by)
            VALUES (:workspace_id, :datasource_id, :table_id, :org_column_id,
                    :org_value_kind, :user_column_id, :user_value_kind, 'migration:v2')
            ON DUPLICATE KEY UPDATE org_column_id=VALUES(org_column_id),
              org_value_kind=VALUES(org_value_kind), user_column_id=VALUES(user_column_id),
              user_value_kind=VALUES(user_value_kind), updated_by='migration:v2'
        """), {
            **item,
            "org_value_kind": item.get("org_value_kind", "id"),
            "user_column_id": item.get("user_column_id"),
            "user_value_kind": item.get("user_value_kind", "id"),
        })


async def verify(session) -> dict[str, int]:
    checks = {
        "users_without_assignment": """SELECT COUNT(*) FROM sys_users u LEFT JOIN sys_assignments a ON a.user_id=u.id AND a.status=1 WHERE a.id IS NULL""",
        "cross_workspace_assignment": """SELECT COUNT(*) FROM sys_assignments a JOIN sys_users u ON u.id=a.user_id JOIN sys_positions p ON p.id=a.position_id WHERE a.workspace_id<>u.workspace_id OR a.workspace_id<>p.workspace_id""",
        "cross_workspace_binding": """SELECT COUNT(*) FROM sys_role_bindings b JOIN sys_positions p ON p.id=b.position_id JOIN sys_roles r ON r.id=b.role_id WHERE b.workspace_id<>p.workspace_id OR b.workspace_id<>r.workspace_id""",
        "users_with_multiple_primary": """SELECT COUNT(*) FROM (SELECT user_id FROM sys_assignments WHERE status=1 AND is_primary=1 GROUP BY user_id HAVING COUNT(*)>1) x""",
        "legacy_capabilities_missing_in_new": """
          SELECT COUNT(*) FROM (
            SELECT DISTINCT u.id user_id, rp.permission_id
            FROM sys_users u JOIN sys_user_roles ur ON ur.user_id=u.id
            JOIN sys_role_permissions rp ON rp.role_id=ur.role_id
            JOIN sys_permissions permission ON permission.id=rp.permission_id AND permission.code<>'*'
          ) old_pair LEFT JOIN (
            SELECT DISTINCT a.user_id, rp.permission_id
            FROM sys_assignments a JOIN sys_role_bindings b ON b.position_id=a.position_id AND b.status=1
            JOIN sys_role_permissions rp ON rp.role_id=b.role_id
            JOIN sys_permissions permission ON permission.id=rp.permission_id AND permission.code<>'*'
            WHERE a.status=1
          ) new_pair ON new_pair.user_id=old_pair.user_id AND new_pair.permission_id=old_pair.permission_id
          WHERE new_pair.user_id IS NULL
        """,
        "new_capabilities_missing_in_legacy": """
          SELECT COUNT(*) FROM (
            SELECT DISTINCT a.user_id, rp.permission_id
            FROM sys_assignments a JOIN sys_role_bindings b ON b.position_id=a.position_id AND b.status=1
            JOIN sys_role_permissions rp ON rp.role_id=b.role_id
            JOIN sys_permissions permission ON permission.id=rp.permission_id AND permission.code<>'*'
            WHERE a.status=1
          ) new_pair LEFT JOIN (
            SELECT DISTINCT u.id user_id, rp.permission_id
            FROM sys_users u JOIN sys_user_roles ur ON ur.user_id=u.id
            JOIN sys_role_permissions rp ON rp.role_id=ur.role_id
            JOIN sys_permissions permission ON permission.id=rp.permission_id AND permission.code<>'*'
          ) old_pair ON old_pair.user_id=new_pair.user_id AND old_pair.permission_id=new_pair.permission_id
          WHERE old_pair.user_id IS NULL
            AND NOT EXISTS (
              SELECT 1 FROM sys_workspaces workspace
              JOIN sys_assignments owner_assignment
                ON owner_assignment.workspace_id=workspace.id
               AND owner_assignment.user_id=workspace.owner_id AND owner_assignment.status=1
              JOIN sys_role_bindings owner_binding
                ON owner_binding.position_id=owner_assignment.position_id AND owner_binding.status=1
              JOIN sys_roles owner_role
                ON owner_role.id=owner_binding.role_id
               AND owner_role.description='authorization-v2 system-managed workspace owner'
              JOIN sys_role_permissions owner_permission
                ON owner_permission.role_id=owner_role.id
               AND owner_permission.permission_id=new_pair.permission_id
              WHERE workspace.owner_id=new_pair.user_id
            )
        """,
        "active_semantic_policies_unmapped": """
          SELECT COUNT(*) FROM semantic_access_policies p
          LEFT JOIN authorization_migration_source_map m
            ON m.source_type='semantic_access_policy' AND CAST(m.source_id AS UNSIGNED)=p.id
          WHERE p.status='active' AND m.id IS NULL
        """,
        "active_bindings_without_version": """
          SELECT COUNT(*) FROM semantic_policy_bindings
          WHERE status=1 AND active_version_id IS NULL
        """,
        "ownership_mapping_blockers": """
          SELECT COUNT(*) FROM authorization_migration_review_items
          WHERE item_type='ownership_mapping_missing' AND status='pending'
        """,
        "exceptions_over_90_days": """
          SELECT COUNT(*) FROM sys_authorization_exceptions
          WHERE TIMESTAMPDIFF(SECOND, starts_at, ends_at) > 90*24*60*60
        """,
        "semantic_non_row_effects_missing": """
          SELECT COUNT(*) FROM semantic_access_policy_effects old_effect
          JOIN semantic_access_policies old_policy
            ON old_policy.id=old_effect.policy_id AND old_policy.status='active'
          JOIN authorization_migration_source_map source_map
            ON BINARY source_map.workspace_id=BINARY old_policy.workspace_id
           AND source_map.source_type='semantic_access_policy'
           AND CAST(source_map.source_id AS UNSIGNED)=old_policy.id
          JOIN semantic_policy_bindings binding
            ON binding.workspace_id=old_policy.workspace_id
           AND binding.datasource_id=old_policy.datasource_id
           AND BINARY binding.target_type=BINARY source_map.target_type
           AND BINARY binding.target_id=BINARY source_map.target_id
          LEFT JOIN semantic_policy_version_effects new_effect
            ON new_effect.version_id=binding.active_version_id
           AND new_effect.asset_type=old_effect.asset_type
           AND new_effect.asset_id=old_effect.asset_id
           AND new_effect.effect_type=old_effect.effect_type
          WHERE old_effect.enabled=1 AND old_effect.effect_type<>'row_filter'
            AND new_effect.id IS NULL
        """,
        "semantic_row_rules_missing": """
          SELECT COUNT(*) FROM semantic_access_policy_effects old_effect
          JOIN semantic_access_policies old_policy
            ON old_policy.id=old_effect.policy_id AND old_policy.status='active'
          JOIN authorization_migration_source_map source_map
            ON BINARY source_map.workspace_id=BINARY old_policy.workspace_id
           AND source_map.source_type='semantic_access_policy'
           AND CAST(source_map.source_id AS UNSIGNED)=old_policy.id
          JOIN semantic_policy_bindings binding
            ON binding.workspace_id=old_policy.workspace_id
           AND binding.datasource_id=old_policy.datasource_id
           AND BINARY binding.target_type=BINARY source_map.target_type
           AND BINARY binding.target_id=BINARY source_map.target_id
          WHERE old_effect.enabled=1 AND old_effect.effect_type='row_filter'
            AND NOT EXISTS (
              SELECT 1 FROM semantic_policy_version_effects new_effect
              WHERE new_effect.version_id=binding.active_version_id
                AND new_effect.asset_type='table'
                AND new_effect.asset_id=old_effect.asset_id
                AND new_effect.effect_type='row_filter'
                AND (
                  new_effect.condition_json=old_effect.condition_json
                  OR JSON_CONTAINS(new_effect.condition_json, old_effect.condition_json, '$.rules')
                )
            )
        """,
        "role_visible_without_authorization_predicate": """
          SELECT COUNT(*) FROM semantic_policy_bindings binding
          JOIN semantic_policy_version_effects visible_effect
            ON visible_effect.version_id=binding.active_version_id
           AND visible_effect.asset_type='table' AND visible_effect.effect_type='visible'
          WHERE binding.status=1 AND binding.target_type='role_binding'
            AND NOT EXISTS (
              SELECT 1 FROM semantic_policy_version_effects row_effect
              WHERE row_effect.version_id=binding.active_version_id
                AND row_effect.asset_type='table'
                AND row_effect.asset_id=visible_effect.asset_id
                AND row_effect.effect_type='row_filter'
                AND (
                  JSON_CONTAINS_PATH(row_effect.condition_json, 'one', '$._authorization_scope')
                  OR JSON_CONTAINS_PATH(row_effect.condition_json, 'one', '$.rules[0]._authorization_scope')
                  OR JSON_CONTAINS_PATH(row_effect.condition_json, 'one', '$.rules[1]._authorization_scope')
                )
            )
        """,
    }
    results = {
        name: int((await session.execute(text(sql))).scalar_one())
        for name, sql in checks.items()
    }
    results["workspace_owner_capabilities_missing"] = int((await session.execute(text("""
        SELECT COUNT(*) FROM sys_workspaces workspace
        CROSS JOIN sys_permissions permission
        WHERE workspace.owner_id IS NOT NULL AND permission.code IN :canonical_codes
          AND NOT EXISTS (
            SELECT 1 FROM sys_assignments assignment
            JOIN sys_role_bindings binding
              ON binding.position_id=assignment.position_id AND binding.status=1
            JOIN sys_role_permissions role_permission
              ON role_permission.role_id=binding.role_id
             AND role_permission.permission_id=permission.id
            WHERE assignment.workspace_id=workspace.id
              AND assignment.user_id=workspace.owner_id AND assignment.status=1
          )
    """).bindparams(bindparam("canonical_codes", expanding=True)), {
        "canonical_codes": sorted(CAPABILITIES),
    })).scalar_one())
    return results


async def rollback(session) -> None:
    async def table_exists(table_name: str) -> bool:
        return bool((await session.execute(text("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema=DATABASE() AND table_name=:table_name
        """), {"table_name": table_name})).scalar_one())

    if await table_exists("authorization_migration_user_backup"):
        await session.execute(text("""
            UPDATE sys_users u JOIN authorization_migration_user_backup b ON BINARY b.user_id=BINARY u.id
            SET u.department_id=b.original_department_id
        """))

    # Remove V2 child records before deleting copied/system roles.  This order also
    # works when the tables were created by SQLAlchemy with real foreign keys.
    for table in (
        "semantic_policy_version_effects", "semantic_policy_versions", "semantic_policy_bindings",
        "semantic_ownership_mappings", "sys_authorization_audit_events", "sys_authorization_exceptions",
        "sys_role_bindings", "sys_assignments", "sys_positions", "sys_authorization_revisions",
        "sys_user_table_permissions_archive", "authorization_migration_review_items",
        "authorization_migration_source_map",
    ):
        await session.execute(text(f"DROP TABLE IF EXISTS {table}"))

    if await table_exists("authorization_migration_role_map"):
        await session.execute(text("""
            DELETE rp FROM sys_role_permissions rp
            JOIN authorization_migration_role_map role_map ON role_map.local_role_id=rp.role_id
        """))
        await session.execute(text("""
            DELETE r FROM sys_roles r
            JOIN authorization_migration_role_map role_map ON role_map.local_role_id=r.id
        """))

    await session.execute(text("""
        DELETE rp FROM sys_role_permissions rp JOIN sys_roles role ON role.id=rp.role_id
        WHERE role.description='authorization-v2 system-managed workspace owner'
    """))
    await session.execute(text("""
        DELETE FROM sys_roles
        WHERE description='authorization-v2 system-managed workspace owner'
    """))
    await session.execute(text("DELETE FROM sys_departments WHERE code='__UNASSIGNED__'"))

    if await table_exists("authorization_migration_wildcard_roles"):
        await session.execute(text("""
            INSERT IGNORE INTO sys_role_permissions (role_id, permission_id)
            SELECT wildcard.role_id, permission.id
            FROM authorization_migration_wildcard_roles wildcard
            JOIN sys_permissions permission ON permission.code='*'
        """))

    for table in (
        "authorization_migration_user_backup", "authorization_migration_role_map",
        "authorization_migration_wildcard_roles",
    ):
        await session.execute(text(f"DROP TABLE IF EXISTS {table}"))
    await session.execute(text("""
        INSERT INTO sys_schema_migrations (migration_id, checksum, status, report_json)
        VALUES (:migration_id, '', 'rolled_back', JSON_OBJECT('status','rolled_back'))
        ON DUPLICATE KEY UPDATE status='rolled_back', report_json=VALUES(report_json), applied_at=NOW()
    """), {"migration_id": MIGRATION_ID})


def emit_report(payload: dict, report_file: str | None = None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if report_file:
        path = Path(report_file).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")


async def run(args) -> None:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        before = await counts(session)
        schema_path = Path(__file__).with_name("authorization_v2.sql")
        checksum = hashlib.sha256(schema_path.read_bytes()).hexdigest()
        target_collations = await migration_collations(session)
        if args.dry_run:
            subject_counts = {}
            try:
                rows = (await session.execute(text("""
                    SELECT JSON_UNQUOTE(JSON_EXTRACT(subject_json,'$.type')) subject_type, COUNT(*) amount
                    FROM semantic_access_policies WHERE status='active'
                    GROUP BY JSON_UNQUOTE(JSON_EXTRACT(subject_json,'$.type'))
                """))).all()
                subject_counts = {str(key): int(value) for key, value in rows}
            except Exception:
                subject_counts = {"unavailable": -1}
            emit_report({
                "mode": "dry-run",
                "source_counts": before,
                "active_semantic_subjects": subject_counts,
                "target_collations": target_collations,
                "schema_sha256": checksum,
                "required_gates": [
                    "database snapshot completed",
                    "ownership mappings present for organization-scoped semantic tables",
                    "zero unexplained functional capability differences",
                    "zero orphan or cross-workspace authorization rows",
                ],
            }, args.report_file)
            return
        if args.rollback:
            await rollback(session)
            emit_report({"mode": "rollback", "status": "complete"}, args.report_file)
            return
        try:
            semantic_tables = (
                "semantic_policy_bindings", "semantic_policy_versions",
                "semantic_policy_version_effects", "semantic_ownership_mappings",
            )
            for statement in statements(schema_path.read_text(encoding="utf-8")):
                domain = "semantic" if any(
                    f"CREATE TABLE IF NOT EXISTS {table}" in statement
                    for table in semantic_tables
                ) else "authorization"
                rendered = statement.replace(
                    "utf8mb4_unicode_ci", target_collations[domain]
                )
                await session.execute(text(rendered))
            ledger = (await session.execute(text("""
                SELECT checksum, status FROM sys_schema_migrations WHERE migration_id=:migration_id
            """), {"migration_id": MIGRATION_ID})).mappings().one_or_none()
            if ledger and ledger["status"] == "complete":
                if ledger["checksum"] != checksum:
                    raise RuntimeError("migration checksum changed after successful application")
                validation = await verify(session)
                if any(validation.values()):
                    raise RuntimeError(f"previous migration no longer validates: {validation}")
                emit_report({"mode": "migrate", "status": "already_applied", "validation": validation}, args.report_file)
                return
            if args.ownership_mapping_file:
                await seed_ownership_mappings(session, args.ownership_mapping_file)
            await backfill(session)
            await migrate_semantic_policies(session)
            validation = await verify(session)
            if any(validation.values()):
                raise RuntimeError(f"authorization migration blocked: {validation}")
            report = {"source_counts": before, "validation": validation, "status": "complete"}
            await session.execute(text("""
                INSERT INTO sys_schema_migrations (migration_id, checksum, status, report_json)
                VALUES (:migration_id, :checksum, 'complete', CAST(:report AS JSON))
                ON DUPLICATE KEY UPDATE checksum=VALUES(checksum), status='complete',
                  report_json=VALUES(report_json), applied_at=NOW()
            """), {
                "migration_id": MIGRATION_ID,
                "checksum": checksum,
                "report": json.dumps(report, ensure_ascii=False),
            })
            emit_report({"mode": "migrate", **report}, args.report_file)
        except Exception as exc:
            await session.rollback()
            failure_report = {"mode": "migrate", "status": "failed", "error": str(exc)}
            try:
                await session.execute(text("""
                    INSERT INTO sys_schema_migrations (migration_id, checksum, status, report_json)
                    VALUES (:migration_id, :checksum, 'failed', CAST(:report AS JSON))
                    ON DUPLICATE KEY UPDATE checksum=VALUES(checksum), status='failed',
                      report_json=VALUES(report_json), applied_at=NOW()
                """), {
                    "migration_id": MIGRATION_ID,
                    "checksum": checksum,
                    "report": json.dumps(failure_report, ensure_ascii=False),
                })
                await session.commit()
            except Exception:
                pass
            emit_report(failure_report, args.report_file)
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--ownership-mapping-file")
    parser.add_argument("--report-file")
    asyncio.run(run(parser.parse_args()))
