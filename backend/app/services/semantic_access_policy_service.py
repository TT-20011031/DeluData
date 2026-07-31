"""Unified semantic access policies for tables, columns, metrics and rows."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.db.database import get_async_db_manager
from app.core.llm.async_llm import get_async_llm
from app.models.auth.organization import DepartmentModel
from app.models.auth.rbac import RoleModel, UserModel
from app.models.config.semantic import (
    SemanticAccessPolicyEffectModel,
    SemanticAccessPolicyModel,
    SemanticAssetTagModel,
    SemanticColumnModel,
    SemanticDatasourceModel,
    SemanticMetricModel,
    SemanticTableModel,
)
logger = logging.getLogger(__name__)
settings = get_settings()

SUBJECT_TYPES = {"all", "role", "user"}
ASSET_TYPES = {"table", "column", "metric"}
VALUE_SOURCES = {
    "current_user.id",
    "current_user.username",
    "current_user.department_id",
    "current_user.scope_dept_ids",
}
MODEL_VERSION = 2
TABLE_DECISIONS = {"visible", "hidden"}
ROW_SCOPE_TYPES = {"all", "self", "department", "custom"}
SUPPORTED_OPERATORS = {
    "=",
    "!=",
    "in",
    "not in",
    "contains",
    "starts_with",
    "between",
    ">",
    ">=",
    "<",
    "<=",
    "is null",
    "is not null",
}


class AccessPolicyError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "access_policy_error",
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _quote_ident(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


class SemanticAccessPolicyService:
    """Deep module: draft, validate, compile, persist and enforce access policy."""

    async def list_policies(
        self,
        workspace_id: str,
        *,
        status: Optional[str] = None,
        datasource_id: Optional[int] = None,
    ) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = await self._get_datasource(session, workspace_id, datasource_id)
            query = select(SemanticAccessPolicyModel).where(
                SemanticAccessPolicyModel.workspace_id == workspace_id,
                SemanticAccessPolicyModel.datasource_id == datasource.id,
                SemanticAccessPolicyModel.model_version == MODEL_VERSION,
            )
            if status:
                query = query.where(SemanticAccessPolicyModel.status == status)
            rows = (
                await session.execute(
                    query.order_by(
                        SemanticAccessPolicyModel.updated_at.desc(),
                        SemanticAccessPolicyModel.id.desc(),
                    )
                )
            ).scalars().all()
            return {
                "datasource_id": datasource.id,
                "policies": [self._serialize_policy(row) for row in rows],
            }

    async def get_policy(self, workspace_id: str, policy_id: int) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            policy = await self._get_policy(session, workspace_id, policy_id)
            return self._serialize_policy(policy)

    async def create_draft(
        self,
        workspace_id: str,
        actor_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = await self._get_datasource(
                session,
                workspace_id,
                payload.get("datasource_id"),
            )
            definition = {
                "name": payload.get("name") or "未命名权限配置",
                "description": payload.get("description"),
                "subject": payload.get("subject") or {},
                "tables": payload.get("tables") or [],
            }
            compilation = await self._compile_definition(
                session,
                workspace_id,
                datasource.id,
                definition,
                schema_fingerprint=datasource.schema_fingerprint,
            )
            policy = SemanticAccessPolicyModel(
                workspace_id=workspace_id,
                datasource_id=datasource.id,
                name=str(definition.get("name") or "未命名权限配置")[:255],
                description=definition.get("description"),
                source_type="natural_language" if str(payload.get("source_text") or "").strip() else "manual",
                source_text=str(payload.get("source_text") or "").strip() or None,
                status="draft",
                subject_json=definition.get("subject") or {},
                asset_selector_json={"tables": definition.get("tables") or []},
                constraint_json=[],
                compile_summary_json=compilation["summary"],
                validation_json=compilation["validation"],
                model_version=MODEL_VERSION,
                schema_fingerprint=datasource.schema_fingerprint,
                created_by=actor_id,
            )
            session.add(policy)
            await session.flush()
            return self._serialize_policy(policy, preview_effects=compilation["effects"])

    async def parse_natural_language(
        self,
        workspace_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Parse natural language into a form patch without persisting or activating it."""
        source_text = str(payload.get("source_text") or "").strip()
        if not source_text:
            raise AccessPolicyError("请输入权限要求", code="invalid_request")
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = await self._get_datasource(session, workspace_id, payload.get("datasource_id"))
            draft_patch = await self._draft_from_natural_language(
                session,
                workspace_id,
                datasource.id,
                source_text,
            )
            compilation = await self._compile_definition(
                session,
                workspace_id,
                datasource.id,
                draft_patch,
                schema_fingerprint=datasource.schema_fingerprint,
            )
            return {
                "draft_patch": draft_patch,
                "validation": compilation["validation"],
                "summary": compilation["summary"],
            }

    async def update_policy(
        self,
        workspace_id: str,
        policy_id: int,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        allowed = {
            "name",
            "description",
            "source_text",
            "subject",
            "tables",
        }
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            policy = await self._get_policy(session, workspace_id, policy_id)
            if policy.status == "active":
                policy = SemanticAccessPolicyModel(
                    workspace_id=policy.workspace_id,
                    datasource_id=policy.datasource_id,
                    name=policy.name,
                    description=policy.description,
                    source_type=policy.source_type,
                    source_text=policy.source_text,
                    source_ref=None,
                    status="draft",
                    subject_json=dict(policy.subject_json or {}),
                    asset_selector_json=dict(policy.asset_selector_json or {}),
                    constraint_json=[],
                    compile_summary_json=dict(policy.compile_summary_json or {}),
                    validation_json=dict(policy.validation_json or {}),
                    model_version=MODEL_VERSION,
                    policy_version=int(policy.policy_version or 1) + 1,
                    schema_fingerprint=policy.schema_fingerprint,
                    created_by=policy.created_by,
                )
                session.add(policy)
                await session.flush()
            for key in allowed:
                if key in patch:
                    if key == "subject":
                        policy.subject_json = patch[key]
                    elif key == "tables":
                        policy.asset_selector_json = {"tables": patch[key]}
                    else:
                        setattr(policy, key, patch[key])
            if policy.status != "draft":
                policy.policy_version = int(policy.policy_version or 1) + 1
            policy.status = "draft"
            policy.activated_by = None
            policy.activated_at = None
            policy.updated_at = datetime.now()
            await session.execute(
                update(SemanticAccessPolicyEffectModel)
                .where(SemanticAccessPolicyEffectModel.policy_id == policy.id)
                .values(enabled=False, updated_at=datetime.now())
            )
            compilation = await self._compile_policy(session, policy)
            policy.compile_summary_json = compilation["summary"]
            policy.validation_json = compilation["validation"]
            policy.schema_fingerprint = compilation["summary"].get("schema_fingerprint")
            session.add(policy)
            await session.flush()
            return self._serialize_policy(policy, preview_effects=compilation["effects"])

    async def compile_policy(self, workspace_id: str, policy_id: int) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            policy = await self._get_policy(session, workspace_id, policy_id)
            compilation = await self._compile_policy(session, policy)
            policy.compile_summary_json = compilation["summary"]
            policy.validation_json = compilation["validation"]
            policy.schema_fingerprint = compilation["summary"].get("schema_fingerprint")
            policy.updated_at = datetime.now()
            session.add(policy)
            await session.flush()
            return {
                "policy": self._serialize_policy(policy),
                **compilation,
            }

    async def activate_policy(
        self,
        workspace_id: str,
        policy_id: int,
        actor_id: str,
    ) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            policy = await self._get_policy(session, workspace_id, policy_id)
            compilation = await self._compile_policy(session, policy)
            blockers = compilation["validation"].get("blockers") or []
            if blockers:
                raise AccessPolicyError(
                    "策略仍有阻断项，无法启用",
                    code="policy_blocked",
                    details={"blockers": blockers},
                )
            active_rows = (
                await session.execute(
                    select(SemanticAccessPolicyModel).where(
                        SemanticAccessPolicyModel.workspace_id == workspace_id,
                        SemanticAccessPolicyModel.datasource_id == policy.datasource_id,
                        SemanticAccessPolicyModel.model_version == MODEL_VERSION,
                        SemanticAccessPolicyModel.status == "active",
                        SemanticAccessPolicyModel.id != policy.id,
                    )
                )
            ).scalars().all()
            subject_key = self._subject_key(policy.subject_json)
            replaced_ids = [row.id for row in active_rows if self._subject_key(row.subject_json) == subject_key]
            if replaced_ids:
                await session.execute(
                    update(SemanticAccessPolicyModel)
                    .where(SemanticAccessPolicyModel.id.in_(replaced_ids))
                    .values(status="disabled", updated_at=datetime.now())
                )
                await session.execute(
                    update(SemanticAccessPolicyEffectModel)
                    .where(SemanticAccessPolicyEffectModel.policy_id.in_(replaced_ids))
                    .values(enabled=False, updated_at=datetime.now())
                )
            await session.execute(
                delete(SemanticAccessPolicyEffectModel).where(
                    SemanticAccessPolicyEffectModel.policy_id == policy.id
                )
            )
            for effect in compilation["effects"]:
                session.add(
                    SemanticAccessPolicyEffectModel(
                        workspace_id=policy.workspace_id,
                        datasource_id=policy.datasource_id,
                        policy_id=policy.id,
                        effect_key=effect["effect_key"],
                        subject_type=effect["subject_type"],
                        subject_id=effect["subject_id"],
                        asset_type=effect["asset_type"],
                        asset_id=effect["asset_id"],
                        effect_type=effect["effect_type"],
                        condition_json=effect.get("condition_json") or {},
                        compiled_sql=effect.get("compiled_sql"),
                        priority=int(effect.get("priority") or 100),
                        enabled=True,
                        policy_version=policy.policy_version,
                    )
                )
            policy.status = "active"
            policy.compile_summary_json = compilation["summary"]
            policy.validation_json = compilation["validation"]
            policy.schema_fingerprint = compilation["summary"].get("schema_fingerprint")
            policy.activated_by = actor_id
            policy.activated_at = datetime.now()
            policy.updated_at = datetime.now()
            session.add(policy)
            await session.flush()
            return self._serialize_policy(policy)

    async def disable_policy(
        self,
        workspace_id: str,
        policy_id: int,
    ) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            policy = await self._get_policy(session, workspace_id, policy_id)
            policy.status = "disabled"
            policy.updated_at = datetime.now()
            await session.execute(
                update(SemanticAccessPolicyEffectModel)
                .where(SemanticAccessPolicyEffectModel.policy_id == policy.id)
                .values(enabled=False, updated_at=datetime.now())
            )
            session.add(policy)
            await session.flush()
            return self._serialize_policy(policy)

    async def load_runtime_access(
        self,
        workspace_id: str,
        datasource_id: int,
        user_access: dict[str, Any],
    ) -> dict[str, Any]:
        from app.services.semantic_policy_binding_service import (
            authorization_v2_is_active,
            load_bound_semantic_runtime,
        )

        if await authorization_v2_is_active():
            bound_runtime = await load_bound_semantic_runtime(
                workspace_id,
                datasource_id,
                str(user_access.get("user_id") or ""),
            )
            # No applicable binding is an explicit deny after the atomic cut-over.
            return bound_runtime or {
                "is_admin": False,
                "effects_by_asset": {},
                "policy_ids": [],
                "authorization_model": "v2_default_deny",
            }
        # Legacy runtime remains readable only before the migration ledger is
        # marked applied, so failed cut-over can still restore the old release.
        if user_access.get("is_admin"):
            return {"is_admin": True, "effects_by_asset": {}, "policy_ids": []}
        subject_filters = [SemanticAccessPolicyEffectModel.subject_type == "all"]
        user_id = str(user_access.get("user_id") or "")
        role_ids = [str(item) for item in user_access.get("role_ids") or []]
        if user_id:
            subject_filters.append(
                (SemanticAccessPolicyEffectModel.subject_type == "user")
                & (SemanticAccessPolicyEffectModel.subject_id == user_id)
            )
        if role_ids:
            subject_filters.append(
                (SemanticAccessPolicyEffectModel.subject_type == "role")
                & (SemanticAccessPolicyEffectModel.subject_id.in_(role_ids))
            )
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            rows = (
                await session.execute(
                    select(SemanticAccessPolicyEffectModel)
                    .join(
                        SemanticAccessPolicyModel,
                        SemanticAccessPolicyModel.id == SemanticAccessPolicyEffectModel.policy_id,
                    )
                    .where(
                        SemanticAccessPolicyEffectModel.workspace_id == workspace_id,
                        SemanticAccessPolicyEffectModel.datasource_id == datasource_id,
                        SemanticAccessPolicyEffectModel.enabled == True,  # noqa: E712
                        SemanticAccessPolicyModel.status == "active",
                        SemanticAccessPolicyModel.model_version == MODEL_VERSION,
                        or_(*subject_filters),
                    )
                    .order_by(SemanticAccessPolicyEffectModel.priority.desc())
                )
            ).scalars().all()
            return self._runtime_from_effects(rows, user_access)

    def check_object_access(
        self,
        object_type: str,
        obj: Any,
        user_access: dict[str, Any],
    ) -> dict[str, Any]:
        label = (
            getattr(obj, "business_name", None)
            or getattr(obj, "physical_name", None)
            or getattr(obj, "name", "")
        )
        if (
            getattr(obj, "status", None) != "confirmed"
            or not getattr(obj, "is_queryable", False)
            or getattr(obj, "sync_state", "current") != "current"
        ):
            return {
                "allowed": False,
                "code": "permission_denied",
                "reason": "object_disabled",
                "label": label,
            }
        runtime = user_access.get("semantic_access") or {}
        if runtime.get("is_admin"):
            return {"allowed": True, "action": "allow_admin", "label": label}
        effects_by_asset = runtime.get("effects_by_asset", {})
        effects = effects_by_asset.get(f"{object_type}:{int(obj.id)}", [])
        table_id = int(obj.id) if object_type == "table" else int(getattr(obj, "table_id", 0) or 0)
        table_effects = effects_by_asset.get(f"table:{table_id}", []) if table_id else []
        table_hidden = [item for item in table_effects if item.get("effect_type") == "hidden"]
        table_visible = [item for item in table_effects if item.get("effect_type") == "visible"]
        if table_hidden:
            return {
                "allowed": False,
                "code": "permission_denied",
                "reason": "explicit_hidden" if object_type == "table" else "parent_hidden",
                "label": label,
                "policy_ids": sorted({int(item["policy_id"]) for item in table_hidden if item.get("policy_id")}),
            }
        if not table_visible:
            return {
                "allowed": False,
                "code": "permission_denied",
                "reason": "default_hidden",
                "label": label,
            }
        hidden = [item for item in effects if item.get("effect_type") == "hidden"]
        if hidden:
            dependency_column_ids = sorted({
                int(column_id)
                for item in hidden
                for column_id in _as_list((item.get("condition_json") or {}).get("dependency_column_ids"))
                if str(column_id).isdigit()
            })
            return {
                "allowed": False,
                "code": "permission_denied",
                "reason": "metric_dependency_hidden" if dependency_column_ids else "explicit_hidden",
                "label": label,
                "policy_ids": sorted({int(item["policy_id"]) for item in hidden if item.get("policy_id")}),
                "dependency_column_ids": dependency_column_ids,
            }
        source_effects = table_visible if object_type != "table" else effects
        return {
            "allowed": True,
            "action": "allow_policy" if object_type == "table" else "allow_inherited",
            "label": label,
            "policy_ids": sorted({int(item["policy_id"]) for item in source_effects if item.get("policy_id")}),
            "sources": [
                {
                    "subject_type": item.get("subject_type"),
                    "subject_id": item.get("subject_id"),
                    "policy_id": item.get("policy_id"),
                }
                for item in source_effects
                if item.get("effect_type") == "visible"
            ],
        }

    def table_requires_row_filter(self, table: Any, user_access: dict[str, Any]) -> bool:
        runtime = user_access.get("semantic_access") or {}
        if runtime.get("is_admin"):
            return False
        effects = runtime.get("effects_by_asset", {}).get(
            f"table:{int(table.id)}",
            [],
        )
        visible = [item for item in effects if item.get("effect_type") == "visible"]
        if not visible:
            return False
        if any((item.get("condition_json") or {}).get("row_scope", {}).get("type") == "all" for item in visible):
            return False
        return True

    def compile_table_predicate(
        self,
        table: Any,
        columns: list[Any],
        user_access: dict[str, Any],
        *,
        alias: str,
    ) -> str:
        runtime = user_access.get("semantic_access") or {}
        if runtime.get("is_admin"):
            return ""
        effects = runtime.get("effects_by_asset", {}).get(
            f"table:{int(table.id)}",
            [],
        )
        if any(item.get("effect_type") == "hidden" for item in effects):
            return "1=0"
        visible = [item for item in effects if item.get("effect_type") == "visible"]
        if not visible:
            return "1=0"
        if any((item.get("condition_json") or {}).get("row_scope", {}).get("type") == "all" for item in visible):
            return ""
        filters = [item for item in effects if item.get("effect_type") == "row_filter"]
        column_by_id = {int(column.id): column for column in columns}
        predicates = []
        for item in filters:
            try:
                predicates.append(
                    self.compile_condition(
                        item.get("condition_json") or {},
                        column_by_id,
                        alias=alias,
                        table_id=int(table.id),
                        user_access=user_access,
                    )
                )
            except AccessPolicyError as exc:
                if exc.code != "row_policy_value_missing":
                    raise
        predicates = [item for item in predicates if item]
        if not predicates:
            return "1=0"
        return "(" + " OR ".join(f"({item})" for item in predicates) + ")"

    def compile_condition(
        self,
        condition: dict[str, Any],
        column_by_id: dict[int, Any],
        *,
        alias: str,
        table_id: int,
        user_access: dict[str, Any],
    ) -> str:
        if not isinstance(condition, dict):
            raise AccessPolicyError("行级条件格式错误", code="invalid_condition")
        if "rules" in condition:
            op = str(condition.get("op") or "AND").upper()
            if op not in {"AND", "OR"}:
                raise AccessPolicyError("条件组只支持 AND 或 OR", code="invalid_condition")
            parts = [
                self.compile_condition(
                    item,
                    column_by_id,
                    alias=alias,
                    table_id=table_id,
                    user_access=user_access,
                )
                for item in _as_list(condition.get("rules"))
            ]
            parts = [item for item in parts if item]
            if not parts:
                raise AccessPolicyError("条件组不能为空", code="invalid_condition")
            return "(" + f" {op} ".join(parts) + ")"

        column_id = condition.get("column_id")
        operator = str(condition.get("operator") or "").strip().lower()
        if not column_id or operator not in SUPPORTED_OPERATORS:
            raise AccessPolicyError("行级条件缺少字段或操作符", code="invalid_condition")
        column = column_by_id.get(int(column_id))
        if not column or int(getattr(column, "table_id", 0)) != table_id:
            raise AccessPolicyError("行级条件字段不属于当前语义表", code="invalid_condition")
        source = condition.get("value_source")
        value = self._resolve_value_source(source, user_access) if source else condition.get("value")
        column_sql = f"{alias}.{_quote_ident(column.physical_name)}" if alias else _quote_ident(column.physical_name)

        if operator == "is null":
            return f"{column_sql} IS NULL"
        if operator == "is not null":
            return f"{column_sql} IS NOT NULL"
        if operator in {"=", "!=", ">", ">=", "<", "<="}:
            if isinstance(value, (list, tuple, dict)):
                raise AccessPolicyError("比较条件只能使用单值", code="invalid_condition")
            return f"{column_sql} {operator} {_sql_literal(value)}"
        if operator in {"in", "not in"}:
            values = (
                [item.strip() for item in re.split(r"[,，]", value) if item.strip()]
                if isinstance(value, str)
                else _as_list(value)
            )
            if not values:
                raise AccessPolicyError("IN 条件解析为空，已拒绝执行", code="row_policy_value_missing")
            return f"{column_sql} {operator.upper()} ({', '.join(_sql_literal(item) for item in values)})"
        if operator == "between":
            values = _as_list(value)
            if len(values) != 2:
                raise AccessPolicyError("BETWEEN 条件必须有两个值", code="invalid_condition")
            return f"{column_sql} BETWEEN {_sql_literal(values[0])} AND {_sql_literal(values[1])}"
        if operator == "contains":
            return f"{column_sql} LIKE {_sql_literal('%' + str(value or '') + '%')}"
        if operator == "starts_with":
            return f"{column_sql} LIKE {_sql_literal(str(value or '') + '%')}"
        raise AccessPolicyError("不支持的行级操作符", code="invalid_condition")

    async def effective_preview(
        self,
        workspace_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        user_id = str(payload.get("user_id") or "")
        if not user_id:
            raise AccessPolicyError("请选择预览用户", code="invalid_request")
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = await self._get_datasource(session, workspace_id, payload.get("datasource_id"))
            user_access = await self._load_user_access(session, workspace_id, user_id)
            runtime = await self._load_runtime_access_in_session(
                session,
                workspace_id,
                datasource.id,
                user_access,
            )
            policy_id = payload.get("policy_id")
            if policy_id:
                policy = await self._get_policy(session, workspace_id, int(policy_id))
                if policy.status != "active":
                    compilation = await self._compile_policy(session, policy)
                    runtime = self._without_subject(runtime, self._subject_key(policy.subject_json))
                    runtime = self._merge_runtime(
                        runtime,
                        self._runtime_from_effects(compilation["effects"], user_access),
                    )
            user_access["semantic_access"] = runtime
            decisions, row_predicates = await self._preview_decisions(
                session,
                workspace_id,
                datasource.id,
                user_access,
                runtime,
            )
            return {
                "user": {
                    key: user_access.get(key)
                    for key in (
                        "user_id",
                        "username",
                        "role_ids",
                        "role_names",
                        "data_scope",
                        "dept_id",
                        "scope_dept_ids",
                    )
                },
                "policy_ids": runtime.get("policy_ids") or [],
                "decisions": decisions,
                "row_predicates": row_predicates,
            }

    async def replace_asset_tags(
        self,
        workspace_id: str,
        actor_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        asset_type = str(payload.get("asset_type") or "")
        asset_id = int(payload.get("asset_id") or 0)
        if asset_type not in ASSET_TYPES or not asset_id:
            raise AccessPolicyError("资产类型或 ID 无效", code="invalid_request")
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = await self._get_datasource(session, workspace_id, payload.get("datasource_id"))
            await self._validate_asset(session, workspace_id, datasource.id, asset_type, asset_id)
            await session.execute(
                delete(SemanticAssetTagModel).where(
                    SemanticAssetTagModel.workspace_id == workspace_id,
                    SemanticAssetTagModel.datasource_id == datasource.id,
                    SemanticAssetTagModel.asset_type == asset_type,
                    SemanticAssetTagModel.asset_id == asset_id,
                )
            )
            tags = []
            for item in payload.get("tags") or []:
                tag_type = str(item.get("tag_type") or "").strip()
                tag_value = str(item.get("tag_value") or "").strip()
                if not tag_type or not tag_value:
                    continue
                row = SemanticAssetTagModel(
                    workspace_id=workspace_id,
                    datasource_id=datasource.id,
                    asset_type=asset_type,
                    asset_id=asset_id,
                    tag_type=tag_type,
                    tag_value=tag_value,
                    source="manual",
                    confidence=1.0,
                    created_by=actor_id,
                )
                session.add(row)
                tags.append({"tag_type": tag_type, "tag_value": tag_value})
            await session.flush()
            return {"asset_type": asset_type, "asset_id": asset_id, "tags": tags}

    async def list_asset_tags(
        self,
        workspace_id: str,
        *,
        datasource_id: Optional[int] = None,
    ) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = await self._get_datasource(session, workspace_id, datasource_id)
            rows = (
                await session.execute(
                    select(SemanticAssetTagModel)
                    .where(
                        SemanticAssetTagModel.workspace_id == workspace_id,
                        SemanticAssetTagModel.datasource_id == datasource.id,
                    )
                    .order_by(
                        SemanticAssetTagModel.asset_type,
                        SemanticAssetTagModel.asset_id,
                        SemanticAssetTagModel.tag_type,
                        SemanticAssetTagModel.tag_value,
                    )
                )
            ).scalars().all()
            return {
                "tags": [
                    {
                        "id": row.id,
                        "asset_type": row.asset_type,
                        "asset_id": row.asset_id,
                        "tag_type": row.tag_type,
                        "tag_value": row.tag_value,
                        "source": row.source,
                        "confidence": row.confidence,
                    }
                    for row in rows
                ]
            }

    async def _compile_policy(self, session, policy: SemanticAccessPolicyModel) -> dict[str, Any]:
        datasource = await self._get_datasource(session, policy.workspace_id, policy.datasource_id)
        definition = {
            "name": policy.name,
            "description": policy.description,
            "subject": policy.subject_json or {},
            "tables": (policy.asset_selector_json or {}).get("tables") or [],
        }
        return await self._compile_definition(
            session,
            policy.workspace_id,
            policy.datasource_id,
            definition,
            schema_fingerprint=datasource.schema_fingerprint,
        )

    async def _compile_definition(
        self,
        session,
        workspace_id: str,
        datasource_id: int,
        definition: dict[str, Any],
        *,
        schema_fingerprint: Optional[str],
    ) -> dict[str, Any]:
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        effects: dict[str, dict[str, Any]] = {}
        subject = self._normalize_subject(definition.get("subject"), blockers)
        subjects = [subject] if subject else []
        await self._validate_subjects(session, workspace_id, subjects, blockers)
        _assets, asset_maps = await self._resolve_assets(
            session,
            workspace_id,
            datasource_id,
            {},
            blockers,
        )
        table_rules = _as_list(definition.get("tables"))
        if not table_rules:
            blockers.append({"code": "tables_empty", "message": "权限配置至少需要一张表"})

        for index, rule in enumerate(table_rules):
            if not isinstance(rule, dict):
                blockers.append({"code": "table_rule_invalid", "message": f"第 {index + 1} 条表规则格式无效"})
                continue
            try:
                table_id = int(rule.get("table_id") or 0)
            except (TypeError, ValueError):
                table_id = 0
            table = asset_maps["table"].get(table_id)
            if not table:
                blockers.append({"code": "table_not_found", "message": f"表不存在：{table_id or '-'}"})
                continue
            decision = str(rule.get("decision") or "").strip().lower() or None
            if decision not in TABLE_DECISIONS | {None}:
                blockers.append({"code": "table_decision_invalid", "message": f"表 {table_id} 的可见状态无效"})
                continue

            hidden_column_ids = self._normalize_ids(rule.get("hidden_column_ids"))
            hidden_metric_ids = self._normalize_ids(rule.get("hidden_metric_ids"))
            invalid_columns = sorted(
                column_id
                for column_id in hidden_column_ids
                if column_id not in asset_maps["column"]
                or int(asset_maps["column"][column_id].table_id) != table_id
            )
            invalid_metrics = sorted(
                metric_id
                for metric_id in hidden_metric_ids
                if metric_id not in asset_maps["metric"]
                or int(asset_maps["metric"][metric_id].table_id) != table_id
            )
            if invalid_columns:
                blockers.append({"code": "columns_not_found", "message": f"表 {table_id} 的字段不存在：{invalid_columns}"})
            if invalid_metrics:
                blockers.append({"code": "metrics_not_found", "message": f"表 {table_id} 的指标不存在：{invalid_metrics}"})

            dependency_hides: dict[int, set[int]] = {}
            table_columns = [
                column for column in asset_maps["column"].values() if int(column.table_id) == table_id
            ]
            for metric in asset_maps["metric"].values():
                if int(metric.table_id) != table_id:
                    continue
                dependency_ids, dependency_error = self._metric_dependency_column_ids(metric, table_columns)
                if dependency_error and decision == "visible" and int(metric.id) not in hidden_metric_ids:
                    blockers.append({
                        "code": "metric_dependency_invalid",
                        "message": f"指标 {getattr(metric, 'business_name', metric.id)} 依赖无效：{dependency_error}",
                    })
                hidden_dependencies = dependency_ids & hidden_column_ids
                if hidden_dependencies:
                    hidden_metric_ids.add(int(metric.id))
                    dependency_hides[int(metric.id)] = hidden_dependencies

            row_scope = rule.get("row_scope")
            if decision != "visible" and row_scope:
                blockers.append({"code": "row_scope_without_grant", "message": f"表 {table_id} 只有明确可见时才能配置行范围"})
            if decision == "hidden" and (hidden_column_ids or hidden_metric_ids):
                warnings.append({"code": "redundant_child_hide", "message": f"表 {table_id} 已隐藏，子资产隐藏规则将被忽略"})

            if subject and decision:
                table_key = f"table:{table_id}:{decision}:{subject['type']}:{subject['id']}"
                normalized_scope = None
                if decision == "visible":
                    normalized_scope = self._normalize_row_scope(
                        row_scope or {"type": "all"},
                        table_id,
                        asset_maps["column"],
                        blockers,
                    )
                effects[table_key] = self._effect(
                    table_key,
                    subject["type"],
                    subject["id"],
                    "table",
                    table_id,
                    decision,
                    condition={"row_scope": normalized_scope or {"type": "all"}},
                    priority=1000 if decision == "hidden" else 100,
                )
                if decision == "visible" and normalized_scope and normalized_scope.get("type") != "all":
                    condition = normalized_scope.get("condition") or {}
                    filter_key = f"table:{table_id}:row_filter:{subject['type']}:{subject['id']}"
                    effects[filter_key] = self._effect(
                        filter_key,
                        subject["type"],
                        subject["id"],
                        "table",
                        table_id,
                        "row_filter",
                        condition=condition,
                        compiled_sql=self._condition_preview(condition, asset_maps["column"]),
                        priority=100,
                    )

            if subject and decision != "hidden":
                for column_id in sorted(hidden_column_ids - set(invalid_columns)):
                    key = f"column:{column_id}:hidden:{subject['type']}:{subject['id']}"
                    effects[key] = self._effect(
                        key, subject["type"], subject["id"], "column", column_id, "hidden", priority=1000
                    )
                for metric_id in sorted(hidden_metric_ids - set(invalid_metrics)):
                    key = f"metric:{metric_id}:hidden:{subject['type']}:{subject['id']}"
                    effects[key] = self._effect(
                        key,
                        subject["type"],
                        subject["id"],
                        "metric",
                        metric_id,
                        "hidden",
                        condition={"dependency_column_ids": sorted(dependency_hides.get(metric_id, set()))},
                        priority=1000,
                    )

        if not effects and not blockers:
            blockers.append({"code": "effects_empty", "message": "配置没有生成任何可见或隐藏规则"})
        affected = {
            asset_type: sorted({item["asset_id"] for item in effects.values() if item["asset_type"] == asset_type})
            for asset_type in ASSET_TYPES
        }
        if schema_fingerprint is None:
            warnings.append({"code": "schema_unversioned", "message": "当前语义目录没有 Schema 指纹，后续变更需重新编译"})
        return {
            "effects": list(effects.values()),
            "validation": {"blockers": blockers, "warnings": warnings},
            "summary": {
                "effect_count": len(effects),
                "subject_count": len(subjects),
                "affected_assets": affected,
                "schema_fingerprint": schema_fingerprint,
                "model_version": MODEL_VERSION,
            },
        }

    def _validate_condition_shape(
        self,
        condition: Any,
        table_id: int,
        columns: dict[int, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(condition, dict):
            return [{"code": "condition_invalid", "message": "行级条件格式无效"}]
        if "rules" in condition:
            op = str(condition.get("op") or "AND").upper()
            errors = [] if op in {"AND", "OR"} else [{"code": "condition_operator_invalid", "message": "条件组只支持 AND/OR"}]
            rules = _as_list(condition.get("rules"))
            if not rules:
                errors.append({"code": "condition_empty", "message": "条件组不能为空"})
            for item in rules:
                errors.extend(self._validate_condition_shape(item, table_id, columns))
            return errors
        try:
            column_id = int(condition.get("column_id") or 0)
        except (TypeError, ValueError):
            column_id = 0
        column = columns.get(column_id)
        operator = str(condition.get("operator") or "").strip().lower()
        errors = []
        if not column or int(getattr(column, "table_id", 0)) != table_id:
            errors.append({"code": "condition_column_invalid", "message": f"行级条件字段不属于表 {table_id}"})
        if operator not in SUPPORTED_OPERATORS:
            errors.append({"code": "condition_operator_invalid", "message": f"不支持的操作符：{operator or '-'}"})
        source = condition.get("value_source")
        if source and source not in VALUE_SOURCES:
            errors.append({"code": "value_source_invalid", "message": f"不支持的动态值：{source}"})
        if operator == "between" and not source and len(_as_list(condition.get("value"))) != 2:
            errors.append({"code": "between_value_invalid", "message": "BETWEEN 条件需要两个静态值"})
        return errors

    async def _resolve_assets(
        self,
        session,
        workspace_id: str,
        datasource_id: int,
        selector: dict[str, Any],
        blockers: list[dict[str, Any]],
    ) -> tuple[dict[str, set[int]], dict[str, dict[int, Any]]]:
        tables = (
            await session.execute(
                select(SemanticTableModel).where(
                    SemanticTableModel.workspace_id == workspace_id,
                    SemanticTableModel.datasource_id == datasource_id,
                )
            )
        ).scalars().all()
        columns = (
            await session.execute(
                select(SemanticColumnModel).where(
                    SemanticColumnModel.workspace_id == workspace_id,
                    SemanticColumnModel.datasource_id == datasource_id,
                )
            )
        ).scalars().all()
        metrics = (
            await session.execute(
                select(SemanticMetricModel).where(
                    SemanticMetricModel.workspace_id == workspace_id,
                    SemanticMetricModel.datasource_id == datasource_id,
                )
            )
        ).scalars().all()
        maps = {
            "table": {int(item.id): item for item in tables},
            "column": {int(item.id): item for item in columns},
            "metric": {int(item.id): item for item in metrics},
        }
        assets = {
            "table_ids": {int(item) for item in _as_list(selector.get("table_ids")) if str(item).isdigit()},
            "column_ids": {int(item) for item in _as_list(selector.get("column_ids")) if str(item).isdigit()},
            "metric_ids": {int(item) for item in _as_list(selector.get("metric_ids")) if str(item).isdigit()},
        }
        tags = [item for item in _as_list(selector.get("tags")) if isinstance(item, dict)]
        for tag in tags:
            asset_type = str(tag.get("asset_type") or "")
            tag_type = str(tag.get("tag_type") or "")
            values = [str(item) for item in _as_list(tag.get("values") or tag.get("tag_value")) if str(item)]
            if asset_type not in ASSET_TYPES or not tag_type or not values:
                blockers.append({"code": "tag_selector_invalid", "message": "标签选择器缺少资产类型、标签类型或值"})
                continue
            rows = (
                await session.execute(
                    select(SemanticAssetTagModel.asset_id).where(
                        SemanticAssetTagModel.workspace_id == workspace_id,
                        SemanticAssetTagModel.datasource_id == datasource_id,
                        SemanticAssetTagModel.asset_type == asset_type,
                        SemanticAssetTagModel.tag_type == tag_type,
                        SemanticAssetTagModel.tag_value.in_(values),
                    )
                )
            ).scalars().all()
            assets[f"{asset_type}_ids"].update(int(item) for item in rows)
        return assets, maps

    def _normalize_ids(self, raw: Any) -> set[int]:
        result: set[int] = set()
        for item in _as_list(raw):
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if value > 0:
                result.add(value)
        return result

    def _subject_key(self, raw: Any) -> tuple[str, str]:
        subject = raw if isinstance(raw, dict) else {}
        subject_type = str(subject.get("type") or "")
        subject_id = "*" if subject_type == "all" else str(subject.get("id") or "")
        return subject_type, subject_id

    def _normalize_subject(
        self,
        raw: Any,
        blockers: list[dict[str, Any]],
    ) -> Optional[dict[str, str]]:
        if not isinstance(raw, dict):
            blockers.append({"code": "subject_invalid", "message": "请选择全体用户、角色或用户"})
            return None
        subject_type = str(raw.get("type") or "").strip().lower()
        subject_id = "*" if subject_type == "all" else str(raw.get("id") or "").strip()
        if subject_type not in SUBJECT_TYPES or not subject_id:
            blockers.append({"code": "subject_invalid", "message": "授权主体必须是全体用户、角色或用户"})
            return None
        return {"type": subject_type, "id": subject_id, "label": str(raw.get("label") or "")}

    def _normalize_row_scope(
        self,
        raw: Any,
        table_id: int,
        columns: dict[int, Any],
        blockers: list[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        if not isinstance(raw, dict):
            blockers.append({"code": "row_scope_invalid", "message": f"表 {table_id} 的行范围格式无效"})
            return None
        scope_type = str(raw.get("type") or "all").strip().lower()
        if scope_type not in ROW_SCOPE_TYPES:
            blockers.append({"code": "row_scope_invalid", "message": f"表 {table_id} 的行范围类型无效"})
            return None
        if scope_type == "all":
            return {"type": "all"}

        if scope_type == "custom":
            condition = raw.get("condition") or {}
            blockers.extend(self._validate_condition_shape(condition, table_id, columns))
            return {"type": "custom", "condition": condition}

        try:
            column_id = int(raw.get("column_id") or 0)
        except (TypeError, ValueError):
            column_id = 0
        column = columns.get(column_id)
        if not column or int(getattr(column, "table_id", 0)) != table_id:
            blockers.append({"code": "row_scope_column_invalid", "message": f"表 {table_id} 的行范围字段无效"})

        if scope_type == "self":
            identity = str(raw.get("identity") or "user_id")
            if identity not in {"user_id", "username"}:
                blockers.append({"code": "row_scope_identity_invalid", "message": "本人范围只支持用户 ID 或用户名"})
                identity = "user_id"
            condition = {
                "column_id": column_id,
                "operator": "=",
                "value_source": "current_user.id" if identity == "user_id" else "current_user.username",
            }
            return {"type": "self", "column_id": column_id, "identity": identity, "condition": condition}

        include_descendants = bool(raw.get("include_descendants", True))
        condition = {
            "column_id": column_id,
            "operator": "in" if include_descendants else "=",
            "value_source": (
                "current_user.scope_dept_ids" if include_descendants else "current_user.department_id"
            ),
        }
        return {
            "type": "department",
            "column_id": column_id,
            "include_descendants": include_descendants,
            "condition": condition,
        }

    async def _validate_subjects(self, session, workspace_id: str, subjects: list[dict[str, str]], blockers: list[dict[str, Any]]) -> None:
        role_ids = [int(item["id"]) for item in subjects if item["type"] == "role" and item["id"].isdigit()]
        invalid_role_ids = [item["id"] for item in subjects if item["type"] == "role" and not item["id"].isdigit()]
        user_ids = [item["id"] for item in subjects if item["type"] == "user"]
        existing_roles = set()
        existing_users = set()
        if role_ids:
            existing_roles = {
                str(item)
                for item in (
                    await session.execute(
                        select(RoleModel.id).where(
                            RoleModel.id.in_(role_ids),
                            or_(RoleModel.workspace_id.is_(None), RoleModel.workspace_id == workspace_id),
                        )
                    )
                ).scalars().all()
            }
        if user_ids:
            existing_users = {
                str(item)
                for item in (
                    await session.execute(
                        select(UserModel.id).where(
                            UserModel.id.in_(user_ids),
                            UserModel.workspace_id == workspace_id,
                        )
                    )
                ).scalars().all()
            }
        missing_roles = invalid_role_ids + [str(item) for item in role_ids if str(item) not in existing_roles]
        missing_users = [item for item in user_ids if item not in existing_users]
        if missing_roles:
            blockers.append({"code": "roles_not_found", "message": f"角色不存在：{missing_roles}"})
        if missing_users:
            blockers.append({"code": "users_not_found", "message": f"用户不存在：{missing_users}"})

    async def _draft_from_natural_language(
        self,
        session,
        workspace_id: str,
        datasource_id: int,
        source_text: str,
    ) -> dict[str, Any]:
        context = await self._natural_language_context(session, workspace_id, datasource_id, source_text)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 DeluData 问数权限表单助手。把管理员的中文要求转换为结构化表单 JSON。"
                    "只能使用目录中给出的用户、角色、表、字段和指标 ID；不确定时不要猜测。"
                    "你只填写表单，不保存、不启用权限。最终权限只有 visible 和 hidden。"
                    "表设为 visible 后字段和指标默认继承可见，只把例外放入 hidden_column_ids "
                    "或 hidden_metric_ids。自定义行范围只能生成结构化条件，禁止输出 SQL。只输出 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "输出结构：{\"name\":\"\",\"description\":\"\","
                    "\"subject\":{\"type\":\"role|user|all\",\"id\":\"\",\"label\":\"\"},"
                    "\"tables\":[{\"table_id\":1,\"decision\":\"visible|hidden\","
                    "\"hidden_column_ids\":[],\"hidden_metric_ids\":[],"
                    "\"row_scope\":{\"type\":\"all|self|department|custom\","
                    "\"column_id\":2,\"identity\":\"user_id|username\",\"include_descendants\":true,"
                    "\"condition\":{\"column_id\":2,\"operator\":\"=\",\"value\":\"\"}}}]}。"
                    "all 行范围不需要 column_id；self、department 必须选择本表字段；custom 使用 condition。\n\n"
                    f"管理员要求：{source_text}\n\n可用语义目录：\n{json.dumps(context, ensure_ascii=False)}"
                ),
            },
        ]
        try:
            parsed = await get_async_llm().generate_json(
                messages,
                model=getattr(settings.llm, "fast_model", settings.llm.model),
                temperature=0,
                max_tokens=3500,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("semantic access policy draft generation failed: %s", exc)
            raise AccessPolicyError("自然语言策略草案生成失败，请稍后重试", code="draft_generation_failed") from exc
        if not isinstance(parsed, dict):
            raise AccessPolicyError("模型没有返回有效策略草案", code="draft_generation_failed")
        return parsed

    async def _natural_language_context(self, session, workspace_id: str, datasource_id: int, text: str) -> dict[str, Any]:
        tables = (
            await session.execute(
                select(SemanticTableModel).where(
                    SemanticTableModel.workspace_id == workspace_id,
                    SemanticTableModel.datasource_id == datasource_id,
                )
            )
        ).scalars().all()
        columns = (
            await session.execute(
                select(SemanticColumnModel).where(
                    SemanticColumnModel.workspace_id == workspace_id,
                    SemanticColumnModel.datasource_id == datasource_id,
                )
            )
        ).scalars().all()
        metrics = (
            await session.execute(
                select(SemanticMetricModel).where(
                    SemanticMetricModel.workspace_id == workspace_id,
                    SemanticMetricModel.datasource_id == datasource_id,
                )
            )
        ).scalars().all()
        roles = (
            await session.execute(
                select(RoleModel).where(or_(RoleModel.workspace_id.is_(None), RoleModel.workspace_id == workspace_id)).limit(200)
            )
        ).scalars().all()
        users = (
            await session.execute(
                select(UserModel).where(UserModel.workspace_id == workspace_id, UserModel.disabled == False).limit(100)  # noqa: E712
            )
        ).scalars().all()
        def score(item: Any) -> tuple[int, int]:
            terms = [
                str(getattr(item, "business_name", "") or ""),
                str(getattr(item, "physical_name", "") or ""),
                str(getattr(item, "name", "") or ""),
                *[str(value) for value in _as_list(getattr(item, "synonyms", []))],
            ]
            hits = sum(1 for value in terms if value and value.lower() in text.lower())
            return hits, -int(getattr(item, "id", 0))

        selected_tables = sorted(tables, key=score, reverse=True)[:80]
        selected_table_ids = {int(item.id) for item in selected_tables}
        selected_columns = sorted(
            [item for item in columns if int(item.table_id) in selected_table_ids],
            key=score,
            reverse=True,
        )[:240]
        return {
            "roles": [{"id": str(item.id), "name": item.name} for item in roles],
            "users": [{"id": str(item.id), "username": item.username, "email": item.email} for item in users],
            "tables": [
                {"id": item.id, "business_name": item.business_name, "physical_name": item.physical_name}
                for item in selected_tables
            ],
            "columns": [
                {
                    "id": item.id,
                    "table_id": item.table_id,
                    "business_name": item.business_name,
                    "physical_name": item.physical_name,
                    "data_type": item.data_type,
                }
                for item in selected_columns
            ],
            "metrics": [
                {"id": item.id, "table_id": item.table_id, "business_name": item.business_name, "name": item.name}
                for item in sorted(metrics, key=score, reverse=True)[:120]
            ],
        }

    async def _load_runtime_access_in_session(
        self,
        session,
        workspace_id: str,
        datasource_id: int,
        user_access: dict[str, Any],
    ) -> dict[str, Any]:
        if user_access.get("is_admin"):
            return {"is_admin": True, "effects_by_asset": {}, "policy_ids": []}
        role_ids = [str(item) for item in user_access.get("role_ids") or []]
        filters = [SemanticAccessPolicyEffectModel.subject_type == "all"]
        filters.append(
            (SemanticAccessPolicyEffectModel.subject_type == "user")
            & (SemanticAccessPolicyEffectModel.subject_id == str(user_access.get("user_id") or ""))
        )
        if role_ids:
            filters.append(
                (SemanticAccessPolicyEffectModel.subject_type == "role")
                & (SemanticAccessPolicyEffectModel.subject_id.in_(role_ids))
            )
        rows = (
            await session.execute(
                select(SemanticAccessPolicyEffectModel)
                .join(SemanticAccessPolicyModel, SemanticAccessPolicyModel.id == SemanticAccessPolicyEffectModel.policy_id)
                .where(
                    SemanticAccessPolicyEffectModel.workspace_id == workspace_id,
                    SemanticAccessPolicyEffectModel.datasource_id == datasource_id,
                    SemanticAccessPolicyEffectModel.enabled == True,  # noqa: E712
                    SemanticAccessPolicyModel.status == "active",
                    SemanticAccessPolicyModel.model_version == MODEL_VERSION,
                    or_(*filters),
                )
            )
        ).scalars().all()
        return self._runtime_from_effects(rows, user_access)

    def _runtime_from_effects(self, rows: list[Any], user_access: dict[str, Any]) -> dict[str, Any]:
        effects_by_asset: dict[str, list[dict[str, Any]]] = {}
        policy_ids = set()
        for row in rows:
            effect = self._serialize_effect(row)
            if not self._subject_matches(effect, user_access):
                continue
            key = f"{effect['asset_type']}:{effect['asset_id']}"
            effects_by_asset.setdefault(key, []).append(effect)
            if effect.get("policy_id"):
                policy_ids.add(int(effect["policy_id"]))
        return {"is_admin": False, "effects_by_asset": effects_by_asset, "policy_ids": sorted(policy_ids)}

    def _subject_matches(self, effect: dict[str, Any], user_access: dict[str, Any]) -> bool:
        subject_type = effect.get("subject_type")
        subject_id = str(effect.get("subject_id") or "")
        if subject_type in {"scope", "all"}:
            return True
        if subject_type == "user":
            return subject_id == str(user_access.get("user_id") or "")
        if subject_type == "role":
            return subject_id in {str(item) for item in user_access.get("role_ids") or []}
        return False

    def _merge_runtime(self, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        merged = {key: list(value) for key, value in (left.get("effects_by_asset") or {}).items()}
        for key, value in (right.get("effects_by_asset") or {}).items():
            merged.setdefault(key, []).extend(value)
        return {
            "is_admin": bool(left.get("is_admin") or right.get("is_admin")),
            "effects_by_asset": merged,
            "policy_ids": sorted(set(left.get("policy_ids") or []) | set(right.get("policy_ids") or [])),
        }

    def _without_subject(
        self,
        runtime: dict[str, Any],
        subject_key: tuple[str, str],
    ) -> dict[str, Any]:
        effects_by_asset: dict[str, list[dict[str, Any]]] = {}
        policy_ids: set[int] = set()
        for key, effects in (runtime.get("effects_by_asset") or {}).items():
            kept = [
                item
                for item in effects
                if (str(item.get("subject_type") or ""), str(item.get("subject_id") or "")) != subject_key
            ]
            if kept:
                effects_by_asset[key] = kept
                policy_ids.update(int(item["policy_id"]) for item in kept if item.get("policy_id"))
        return {
            "is_admin": bool(runtime.get("is_admin")),
            "effects_by_asset": effects_by_asset,
            "policy_ids": sorted(policy_ids),
        }

    async def _preview_decisions(self, session, workspace_id: str, datasource_id: int, user_access: dict[str, Any], runtime: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        tables = list(
            (
                await session.execute(
                    select(SemanticTableModel).where(
                        SemanticTableModel.workspace_id == workspace_id,
                        SemanticTableModel.datasource_id == datasource_id,
                    )
                )
            ).scalars().all()
        )
        columns = list(
            (
                await session.execute(
                    select(SemanticColumnModel).where(
                        SemanticColumnModel.workspace_id == workspace_id,
                        SemanticColumnModel.datasource_id == datasource_id,
                    )
                )
            ).scalars().all()
        )
        metrics = list(
            (
                await session.execute(
                    select(SemanticMetricModel).where(
                        SemanticMetricModel.workspace_id == workspace_id,
                        SemanticMetricModel.datasource_id == datasource_id,
                    )
                )
            ).scalars().all()
        )
        columns_by_table: dict[int, list[Any]] = {}
        column_by_id = {int(item.id): item for item in columns}
        for column in columns:
            columns_by_table.setdefault(int(column.table_id), []).append(column)

        decisions: list[dict[str, Any]] = []
        decision_by_asset: dict[str, dict[str, Any]] = {}
        for asset_type, rows in (("table", tables), ("column", columns)):
            for item in rows:
                decision = self.check_object_access(asset_type, item, user_access)
                payload = {
                    "asset_type": asset_type,
                    "asset_id": int(item.id),
                    "table_id": int(item.id) if asset_type == "table" else int(item.table_id),
                    "asset_name": getattr(item, "business_name", None) or getattr(item, "physical_name", None),
                    **decision,
                }
                decisions.append(payload)
                decision_by_asset[f"{asset_type}:{int(item.id)}"] = payload

        for metric in metrics:
            decision = self.check_object_access("metric", metric, user_access)
            if decision.get("allowed"):
                dependency_ids, dependency_error = self._metric_dependency_column_ids(
                    metric,
                    columns_by_table.get(int(metric.table_id), []),
                )
                hidden_dependencies = [
                    column_id
                    for column_id in dependency_ids
                    if not self.check_object_access("column", column_by_id[column_id], user_access).get("allowed")
                ]
                if dependency_error or hidden_dependencies:
                    decision = {
                        "allowed": False,
                        "code": "permission_denied",
                        "reason": "metric_dependency_hidden" if hidden_dependencies else "metric_dependency_invalid",
                        "label": getattr(metric, "business_name", None) or getattr(metric, "name", ""),
                        "dependency_column_ids": hidden_dependencies,
                    }
            payload = {
                "asset_type": "metric",
                "asset_id": int(metric.id),
                "table_id": int(metric.table_id),
                "asset_name": getattr(metric, "business_name", None) or getattr(metric, "name", None),
                **decision,
            }
            decisions.append(payload)
            decision_by_asset[f"metric:{int(metric.id)}"] = payload

        row_predicates: list[dict[str, Any]] = []
        for table in tables:
            table_decision = decision_by_asset.get(f"table:{int(table.id)}") or {}
            if not table_decision.get("allowed"):
                continue
            sql = self.compile_table_predicate(
                table,
                columns_by_table.get(int(table.id), []),
                user_access,
                alias="t",
            )
            row_predicates.append(
                {
                    "table_id": int(table.id),
                    "status": "blocked" if sql == "1=0" else "ready",
                    "scope": "none" if sql == "1=0" else "all" if not sql else "filtered",
                    "sql": sql,
                    "message": "当前匹配授权无法解析出可见行" if sql == "1=0" else None,
                }
            )
        return decisions, row_predicates

    async def _load_user_access(self, session, workspace_id: str, user_id: str) -> dict[str, Any]:
        user = (
            await session.execute(
                select(UserModel)
                .where(UserModel.id == user_id, UserModel.workspace_id == workspace_id)
                .options(selectinload(UserModel.roles).selectinload(RoleModel.permissions))
            )
        ).scalar_one_or_none()
        if not user:
            raise AccessPolicyError("用户不存在", code="not_found")
        role_ids = [str(role.id) for role in user.roles]
        role_names = [str(role.name) for role in user.roles]
        data_scope = 1 if user.is_admin else 4
        if not user.is_admin:
            for role in user.roles:
                if role.data_scope and int(role.data_scope) < data_scope:
                    data_scope = int(role.data_scope)
        dept_id = getattr(user, "department_id", None)
        scope_dept_ids = [int(dept_id)] if dept_id else []
        if dept_id and data_scope == 2:
            result = await session.execute(
                select(DepartmentModel.id).where(
                    DepartmentModel.workspace_id == workspace_id,
                    DepartmentModel.status == True,  # noqa: E712
                    or_(
                        DepartmentModel.id == int(dept_id),
                        DepartmentModel.ancestors.like(f"%/{int(dept_id)}/%"),
                    ),
                )
            )
            scope_dept_ids = sorted({int(item) for item in result.scalars().all()})
        return {
            "user_id": str(user.id),
            "username": user.username,
            "role_ids": role_ids,
            "role_names": role_names,
            "is_admin": bool(user.is_admin),
            "data_scope": data_scope,
            "dept_id": dept_id,
            "scope_dept_ids": scope_dept_ids,
        }

    def _resolve_value_source(self, source: Any, user_access: dict[str, Any]) -> Any:
        source = str(source or "")
        mapping = {
            "current_user.id": user_access.get("user_id"),
            "current_user.username": user_access.get("username"),
            "current_user.department_id": user_access.get("dept_id"),
            "current_user.scope_dept_ids": user_access.get("scope_dept_ids"),
        }
        if source not in mapping:
            raise AccessPolicyError(f"不支持的动态值来源：{source}", code="value_source_invalid")
        value = mapping[source]
        if value is None or value == "" or value == []:
            raise AccessPolicyError(f"当前用户缺少策略所需属性：{source}", code="row_policy_value_missing")
        return value

    async def _get_datasource(self, session, workspace_id: str, datasource_id: Optional[int]) -> SemanticDatasourceModel:
        query = select(SemanticDatasourceModel).where(
            SemanticDatasourceModel.workspace_id == workspace_id,
            SemanticDatasourceModel.is_active == True,  # noqa: E712
        )
        if datasource_id:
            query = query.where(SemanticDatasourceModel.id == int(datasource_id))
        datasource = (
            await session.execute(query.order_by(SemanticDatasourceModel.updated_at.desc()))
        ).scalars().first()
        if not datasource:
            raise AccessPolicyError("当前工作区没有可用语义数据源", code="not_found")
        return datasource

    async def _get_policy(self, session, workspace_id: str, policy_id: int) -> SemanticAccessPolicyModel:
        policy = (
            await session.execute(
                select(SemanticAccessPolicyModel).where(
                    SemanticAccessPolicyModel.id == int(policy_id),
                    SemanticAccessPolicyModel.workspace_id == workspace_id,
                    SemanticAccessPolicyModel.model_version == MODEL_VERSION,
                )
            )
        ).scalar_one_or_none()
        if not policy:
            raise AccessPolicyError("权限策略不存在", code="not_found")
        return policy

    async def _validate_asset(self, session, workspace_id: str, datasource_id: int, asset_type: str, asset_id: int) -> None:
        model = {"table": SemanticTableModel, "column": SemanticColumnModel, "metric": SemanticMetricModel}[asset_type]
        exists = (
            await session.execute(
                select(model.id).where(
                    model.id == asset_id,
                    model.workspace_id == workspace_id,
                    model.datasource_id == datasource_id,
                )
            )
        ).scalar_one_or_none()
        if not exists:
            raise AccessPolicyError("语义资产不存在", code="not_found")

    def _effect(
        self,
        key: str,
        subject_type: str,
        subject_id: str,
        asset_type: str,
        asset_id: int,
        effect_type: str,
        *,
        condition: Optional[dict[str, Any]] = None,
        compiled_sql: Optional[str] = None,
        priority: int = 100,
    ) -> dict[str, Any]:
        return {
            "policy_id": 0,
            "effect_key": key[:128],
            "subject_type": subject_type,
            "subject_id": str(subject_id),
            "asset_type": asset_type,
            "asset_id": int(asset_id),
            "effect_type": effect_type,
            "condition_json": condition or {},
            "compiled_sql": compiled_sql,
            "priority": priority,
        }

    def _condition_preview(self, condition: dict[str, Any], columns: dict[int, Any]) -> str:
        if "rules" in condition:
            op = str(condition.get("op") or "AND").upper()
            return "(" + f" {op} ".join(self._condition_preview(item, columns) for item in _as_list(condition.get("rules"))) + ")"
        column = columns.get(int(condition.get("column_id") or 0))
        column_name = getattr(column, "physical_name", f"column_{condition.get('column_id')}")
        value = f"${{{condition.get('value_source')}}}" if condition.get("value_source") else repr(condition.get("value"))
        return f"{_quote_ident(column_name)} {str(condition.get('operator') or '').upper()} {value}"

    def _metric_dependency_column_ids(
        self,
        metric: Any,
        table_columns: list[Any],
    ) -> tuple[set[int], Optional[str]]:
        dependency_ids = {
            int(value)
            for value in (getattr(metric, "column_id", None), getattr(metric, "time_column_id", None))
            if value
        }
        placeholders = re.findall(
            r"\{([A-Za-z_][\w$]*|[\u4e00-\u9fff][\w\u4e00-\u9fff]*)\}",
            str(getattr(metric, "formula", "") or ""),
        )
        for name in placeholders:
            normalized = str(name).strip().lower()
            matches = [
                column
                for column in table_columns
                if normalized
                in {
                    str(getattr(column, "physical_name", "") or "").strip().lower(),
                    str(getattr(column, "business_name", "") or "").strip().lower(),
                }
            ]
            if len(matches) != 1:
                return dependency_ids, f"指标字段无法唯一解析：{name}"
            dependency_ids.add(int(matches[0].id))
        known_ids = {int(item.id) for item in table_columns}
        if dependency_ids - known_ids:
            return dependency_ids, "指标依赖了不存在或跨表的字段"
        return dependency_ids, None

    def _serialize_policy(self, policy: SemanticAccessPolicyModel, *, preview_effects: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        payload = {
            "id": policy.id,
            "workspace_id": policy.workspace_id,
            "datasource_id": policy.datasource_id,
            "name": policy.name,
            "description": policy.description,
            "source_type": policy.source_type,
            "source_text": policy.source_text,
            "source_ref": policy.source_ref,
            "status": policy.status,
            "subject": policy.subject_json or {},
            "tables": (policy.asset_selector_json or {}).get("tables") or [],
            "compile_summary_json": policy.compile_summary_json or {},
            "validation_json": policy.validation_json or {},
            "model_version": int(getattr(policy, "model_version", MODEL_VERSION) or MODEL_VERSION),
            "policy_version": policy.policy_version,
            "schema_fingerprint": policy.schema_fingerprint,
            "created_by": policy.created_by,
            "activated_by": policy.activated_by,
            "activated_at": policy.activated_at,
            "created_at": policy.created_at,
            "updated_at": policy.updated_at,
        }
        if preview_effects is not None:
            payload["preview_effects"] = preview_effects
        return payload

    def _serialize_effect(self, row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            return dict(row)
        return {
            "id": row.id,
            "policy_id": row.policy_id,
            "effect_key": row.effect_key,
            "subject_type": row.subject_type,
            "subject_id": row.subject_id,
            "asset_type": row.asset_type,
            "asset_id": row.asset_id,
            "effect_type": row.effect_type,
            "condition_json": row.condition_json or {},
            "compiled_sql": row.compiled_sql,
            "priority": row.priority,
        }


_service: Optional[SemanticAccessPolicyService] = None


def get_semantic_access_policy_service() -> SemanticAccessPolicyService:
    global _service
    if _service is None:
        _service = SemanticAccessPolicyService()
    return _service
