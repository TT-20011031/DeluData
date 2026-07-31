"""Expand and backfill permission-evidence v2 without publishing new policies.

Run without arguments to print an impact report. Apply only after reviewing it::

    python -m migrations.run_permission_evidence_v2_migration --apply

The migration is idempotent. Legacy columns remain available for the dual-protocol
rollout and must be removed only after the compatible backend and new frontend have
been deployed and legacy traffic has reached zero.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db.database import get_async_db_manager  # noqa: E402
from app.models.config.permission_evidence import (  # noqa: E402
    SemanticAccessEvidenceSetModel,
)
from app.services.permission_evidence_service import (  # noqa: E402
    PermissionEvidenceService,
)


ACTION = "semantic_access.permission_evidence_v2_migration"


async def _column_exists(session, table_name: str, column_name: str) -> bool:
    return bool((await session.execute(text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema=DATABASE() AND table_name=:table_name "
        "AND column_name=:column_name"
    ), {"table_name": table_name, "column_name": column_name})).scalar())


async def _add_column(session, table_name: str, column_name: str, ddl: str) -> None:
    if not await _column_exists(session, table_name, column_name):
        await session.execute(text(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"
        ))


def _scope(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    return value if isinstance(value, dict) else {}


def _legacy_signature(
    assets: list[dict[str, Any]], relations: list[dict[str, Any]],
) -> tuple:
    baseline = sorted(
        int(row["table_id"]) for row in assets
        if row["asset_type"] == "table"
        and row["access_class"] == "workspace_public"
        and not row["is_sensitive"]
    )
    table_grants = sorted(
        (
            int(row["org_unit_id"]),
            int(row["table_id"]),
            json.dumps(_scope(row["row_scope_json"]) or {"type": "all"}, sort_keys=True),
        )
        for row in relations
        if row["asset_type"] == "table" and row["relation_role"] != "none"
    )
    fields = sorted(
        (
            int(row["org_unit_id"]),
            int(row["table_id"]),
            int(row["asset_id"]),
            row["access_decision"] if row["access_decision"] in {"visible", "hidden"} else "hidden",
        )
        for row in relations if row["asset_type"] == "column"
    )
    return tuple(baseline), tuple(table_grants), tuple(fields)


def _v2_signature(
    assets: list[dict[str, Any]], relations: list[dict[str, Any]],
) -> tuple:
    baseline = sorted(
        int(row["table_id"]) for row in assets
        if row["asset_type"] == "table"
        and row["baseline_access"] == "workspace_visible"
        and not row["is_sensitive"]
    )
    table_grants = sorted(
        (
            int(row["org_unit_id"]),
            int(row["table_id"]),
            json.dumps(
                _scope(row["row_scope_json"]) or {"type": "all"}
                if row["access_level"] == "partial" else {"type": "all"},
                sort_keys=True,
            ),
        )
        for row in relations
        if row["asset_type"] == "table"
        and row["access_level"] in {"visible", "partial"}
    )
    fields = sorted(
        (
            int(row["org_unit_id"]),
            int(row["table_id"]),
            int(row["asset_id"]),
            row["field_decision"],
        )
        for row in relations if row["asset_type"] == "column"
    )
    return tuple(baseline), tuple(table_grants), tuple(fields)


async def _evidence_rows(session, *, include_v2: bool) -> tuple[list[dict], list[dict]]:
    asset_fields = (
        "id,evidence_set_id,asset_type,asset_id,table_id,access_class,"
        "is_sensitive,review_status"
    )
    relation_fields = (
        "id,evidence_set_id,asset_type,asset_id,table_id,org_unit_id,"
        "relation_role,access_decision,row_scope_json,review_status"
    )
    if include_v2:
        asset_fields += ",baseline_access,requires_individual_review"
        relation_fields += ",business_role,access_level,field_decision"
    assets = [dict(row) for row in (await session.execute(text(
        f"SELECT {asset_fields} FROM semantic_access_evidence_assets"
    ))).mappings()]
    relations = [dict(row) for row in (await session.execute(text(
        f"SELECT {relation_fields} FROM semantic_access_evidence_relations"
    ))).mappings()]
    return assets, relations


async def _report(session) -> dict[str, int]:
    counts = dict((await session.execute(text(
        "SELECT "
        "(SELECT COUNT(*) FROM semantic_access_evidence_sets) evidence_sets,"
        "(SELECT COUNT(*) FROM semantic_access_evidence_assets "
        " WHERE asset_type='table' AND access_class='workspace_public') public_tables,"
        "(SELECT COUNT(*) FROM semantic_access_evidence_assets "
        " WHERE asset_type='table' AND (access_class='restricted' OR is_sensitive=1)) high_risk_tables,"
        "(SELECT COUNT(*) FROM semantic_access_evidence_relations "
        " WHERE asset_type='table' AND relation_role='none') hidden_relations,"
        "(SELECT COUNT(*) FROM semantic_access_evidence_relations "
        " WHERE asset_type='column' AND access_decision='hidden') hidden_fields"
    ))).mappings().one())
    published_conditional_all = int((await session.execute(text(
        "SELECT COUNT(*) FROM semantic_access_evidence_relations r "
        "JOIN semantic_access_evidence_sets s ON s.id=r.evidence_set_id "
        "WHERE s.status='published' AND r.asset_type='table' "
        "AND r.relation_role='conditional_consumer' "
        "AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(r.row_scope_json,'$.type')),'all')='all'"
    ))).scalar() or 0)
    published_sensitive_public = int((await session.execute(text(
        "SELECT COUNT(*) FROM semantic_access_evidence_assets a "
        "JOIN semantic_access_evidence_sets s ON s.id=a.evidence_set_id "
        "WHERE s.status='published' AND a.asset_type='table' "
        "AND a.is_sensitive=1 AND a.access_class='workspace_public'"
    ))).scalar() or 0)
    published_invalid_field_decision = int((await session.execute(text(
        "SELECT COUNT(*) FROM semantic_access_evidence_relations r "
        "JOIN semantic_access_evidence_sets s ON s.id=r.evidence_set_id "
        "WHERE s.status='published' AND r.asset_type='column' "
        "AND r.access_decision NOT IN ('visible','hidden')"
    ))).scalar() or 0)
    counts["published_conditional_all"] = published_conditional_all
    counts["published_sensitive_public"] = published_sensitive_public
    counts["published_invalid_field_decision"] = published_invalid_field_decision
    return {key: int(value or 0) for key, value in counts.items()}


async def run_migration(*, apply: bool = False) -> None:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        report = await _report(session)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if (
            report["published_conditional_all"]
            or report["published_sensitive_public"]
            or report["published_invalid_field_decision"]
        ):
            raise RuntimeError(
                "发现已发布的 conditional_consumer+all、敏感公共表或非法字段决定；"
                "为避免权限变化，必须先人工处理对应工作区。"
            )
        if not apply:
            print("Report only. Re-run with --apply after reviewing the impact.")
            return

        await _add_column(
            session, "semantic_access_evidence_sets", "model_version",
            "INT NOT NULL DEFAULT 1",
        )
        await _add_column(
            session, "semantic_access_evidence_assets", "baseline_access",
            "VARCHAR(24) NULL",
        )
        await _add_column(
            session, "semantic_access_evidence_assets", "requires_individual_review",
            "TINYINT(1) NOT NULL DEFAULT 0",
        )
        await _add_column(
            session, "semantic_access_evidence_relations", "business_role",
            "VARCHAR(32) NULL",
        )
        await _add_column(
            session, "semantic_access_evidence_relations", "access_level",
            "VARCHAR(16) NULL",
        )
        await _add_column(
            session, "semantic_access_evidence_relations", "field_decision",
            "VARCHAR(16) NULL",
        )

        legacy_assets, legacy_relations = await _evidence_rows(
            session, include_v2=False,
        )
        statuses = {
            int(row["id"]): row["status"]
            for row in (await session.execute(text(
                "SELECT id,status FROM semantic_access_evidence_sets"
            ))).mappings()
        }
        old_signatures: dict[int, tuple] = {}
        assets_by_set: dict[int, list[dict]] = defaultdict(list)
        relations_by_set: dict[int, list[dict]] = defaultdict(list)
        for row in legacy_assets:
            assets_by_set[int(row["evidence_set_id"])].append(row)
        for row in legacy_relations:
            relations_by_set[int(row["evidence_set_id"])].append(row)
        for set_id, status in statuses.items():
            if status == "published":
                old_signatures[set_id] = _legacy_signature(
                    assets_by_set[set_id], relations_by_set[set_id],
                )

        await session.execute(text(
            "UPDATE semantic_access_evidence_assets SET "
            "baseline_access=CASE WHEN access_class='workspace_public' "
            "THEN 'workspace_visible' ELSE 'controlled' END,"
            "requires_individual_review=CASE "
            "WHEN is_sensitive=1 OR access_class='restricted' THEN 1 ELSE 0 END,"
            "review_status=CASE "
            "WHEN review_status='pending' AND "
            "(asset_type='column' OR access_class<>'workspace_public') "
            "THEN 'auto_safe' ELSE review_status END"
        ))
        await session.execute(text(
            "UPDATE semantic_access_evidence_assets a "
            "JOIN semantic_access_evidence_sets s ON s.id=a.evidence_set_id SET "
            "a.baseline_access='controlled',a.requires_individual_review=1,"
            "a.access_class='restricted',"
            "a.review_status=CASE WHEN a.review_status='pending' "
            "THEN 'auto_safe' ELSE a.review_status END,"
            "a.evidence_json=JSON_ARRAY_APPEND("
            "COALESCE(a.evidence_json,JSON_ARRAY()),'$','migration_v2:sensitive_public_fail_closed') "
            "WHERE s.status<>'published' AND a.asset_type='table' "
            "AND a.is_sensitive=1 AND a.access_class='workspace_public'"
        ))
        await session.execute(text(
            "UPDATE semantic_access_evidence_relations SET "
            "business_role=relation_role,"
            "access_level=CASE WHEN asset_type<>'table' THEN NULL "
            "WHEN relation_role='none' THEN 'hidden' "
            "WHEN COALESCE(JSON_UNQUOTE(JSON_EXTRACT(row_scope_json,'$.type')),'all')='all' "
            "THEN 'visible' ELSE 'partial' END,"
            "field_decision=CASE WHEN asset_type='column' AND access_decision='visible' "
            "THEN 'visible' WHEN asset_type='column' THEN 'hidden' ELSE NULL END"
        ))
        await session.execute(text(
            "UPDATE semantic_access_evidence_relations r "
            "JOIN semantic_access_evidence_sets s ON s.id=r.evidence_set_id SET "
            "r.access_level='hidden',r.row_scope_json=JSON_OBJECT('type','all'),"
            "r.review_status=CASE WHEN r.review_status='pending' "
            "THEN 'auto_safe' ELSE r.review_status END,"
            "r.evidence_json=JSON_ARRAY_APPEND("
            "COALESCE(r.evidence_json,JSON_ARRAY()),'$','migration_v2:conditional_all_fail_closed') "
            "WHERE s.status<>'published' AND r.asset_type='table' "
            "AND r.relation_role='conditional_consumer' "
            "AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(r.row_scope_json,'$.type')),'all')='all'"
        ))
        await session.execute(text(
            "UPDATE semantic_access_evidence_relations SET review_status='auto_safe' "
            "WHERE review_status='pending' AND ("
            "(asset_type='table' AND access_level='hidden') OR "
            "(asset_type='column' AND field_decision='hidden'))"
        ))
        await session.execute(text(
            "UPDATE semantic_access_evidence_sets SET model_version=2"
        ))
        await session.flush()

        v2_assets, v2_relations = await _evidence_rows(session, include_v2=True)
        v2_assets_by_set: dict[int, list[dict]] = defaultdict(list)
        v2_relations_by_set: dict[int, list[dict]] = defaultdict(list)
        for row in v2_assets:
            v2_assets_by_set[int(row["evidence_set_id"])].append(row)
        for row in v2_relations:
            v2_relations_by_set[int(row["evidence_set_id"])].append(row)
        mismatches = [
            set_id for set_id, old_signature in old_signatures.items()
            if old_signature != _v2_signature(
                v2_assets_by_set[set_id], v2_relations_by_set[set_id],
            )
        ]
        if mismatches:
            raise RuntimeError(
                f"已发布依据集迁移前后不等价，已回滚：{mismatches[:20]}"
            )

        service = object.__new__(PermissionEvidenceService)
        evidence_sets = list((await session.execute(select(
            SemanticAccessEvidenceSetModel,
        ))).scalars())
        for evidence_set in evidence_sets:
            await service._refresh_progress(session, evidence_set)

        workspaces = [row for row in (await session.execute(text(
            "SELECT workspace_id,COUNT(*) evidence_set_count "
            "FROM semantic_access_evidence_sets GROUP BY workspace_id"
        ))).mappings()]
        for row in workspaces:
            exists = (await session.execute(text(
                "SELECT id FROM sys_authorization_audit_events "
                "WHERE workspace_id=:workspace_id AND action=:action LIMIT 1"
            ), {"workspace_id": row["workspace_id"], "action": ACTION})).scalar_one_or_none()
            if exists is not None:
                continue
            await session.execute(text(
                "INSERT INTO sys_authorization_audit_events "
                "(workspace_id,actor_id,action,target_type,target_id,"
                "before_json,after_json,reason,created_at) VALUES "
                "(:workspace_id,'system:migration',:action,'permission_evidence',NULL,"
                "JSON_OBJECT('model_version',1),"
                "JSON_OBJECT('model_version',2,'evidence_sets',:evidence_sets),"
                "'Permission evidence v2 fail-closed migration',NOW())"
            ), {
                "workspace_id": row["workspace_id"],
                "action": ACTION,
                "evidence_sets": int(row["evidence_set_count"] or 0),
            })
    print("Permission evidence v2 migration complete; active policies unchanged.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true",
        help="apply the v2 expand/backfill after reviewing the report",
    )
    asyncio.run(run_migration(apply=parser.parse_args().apply))
