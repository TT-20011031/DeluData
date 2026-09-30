"""Database configuration and readonly query APIs."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.core.db.database import get_async_db_manager
from app.core.db.mysql_connection_policy import create_mysql_engine
from app.core.security.auth import User
from app.models.auth.organization import DepartmentModel
from app.models.auth.rbac import RoleModel, UserModel
from app.models.config.db_config import (
    DBConnectionStatus,
    TableSchema,
    UserDBConfig,
    activate_user_connection,
    disconnect_user,
    disconnect_user_async,
    disconnect_workspace_async,
    get_user_db_config,
    get_user_db_config_async,
    get_workspace_admin_db_config_async,
    get_workspace_db_config_async,
    save_workspace_db_config_async,
    save_user_db_config,
    save_user_db_config_async,
)
from app.models.config.semantic import SemanticColumnModel, SemanticDatasourceModel, SemanticTableModel
from app.services.semantic_access_policy_service import get_semantic_access_policy_service
from app.services.semantic_policy_binding_service import authorization_v2_is_active
from app.services.db_whitelist_service import DBWhitelistService

router = APIRouter()
logger = logging.getLogger(__name__)

DB_CONNECT_CODE_NOT_FOUND = "DB_NOT_FOUND"
DB_CONNECT_CODE_AUTH_FAILED = "AUTH_FAILED"
DB_CONNECT_CODE_HOST_UNREACHABLE = "HOST_UNREACHABLE"
DB_CONNECT_CODE_UNKNOWN = "UNKNOWN"


class ConnectRequest(BaseModel):
    host: str
    port: int = 3306
    username: str
    password: str
    database: str


class ConnectResponse(BaseModel):
    status: str
    message: str
    tables: list[str] = []


class SchemaResponse(BaseModel):
    database: str
    tables: list[TableSchema]
    table_names: list[str] = []


class TableDataRequest(BaseModel):
    table_name: str
    page: int = 1
    page_size: int = 50


class TableDataResponse(BaseModel):
    table_name: str
    columns: list[str]
    rows: list[list]
    total_count: int
    page: int
    page_size: int
    has_more: bool


class QueryRequest(BaseModel):
    sql: str
    timeout_sec: int = 5
    max_rows: int = 1000


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool
    execution_time_ms: float
    error: Optional[str] = None


class ReadonlyConfigRequest(BaseModel):
    readonly_username: str
    readonly_password: str


class TableDescriptionRequest(BaseModel):
    table_name: str
    description: str


_table_descriptions: dict[str, dict[str, str]] = {}
_whitelist_service = DBWhitelistService()


def _is_admin_user(user: User) -> bool:
    return (
        user.capability_scopes.get("database:manage") == "*"
        and not user.denied_capability_scopes.get("database:manage")
    )


def _can_query_sql(user: User) -> bool:
    return (
        _is_admin_user(user)
        or user.capability_scopes.get("database:query") is not None
        and user.denied_capability_scopes.get("database:query") != "*"
    )


def _require_admin_db_management(user: User) -> None:
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="仅管理员可以管理数据库连接")


def _build_capabilities(user: User, *, can_ask: bool = False) -> dict[str, bool]:
    is_admin = _is_admin_user(user)
    return {
        "can_manage_connection": is_admin,
        "can_manage_semantic": is_admin,
        "can_query_sql": _can_query_sql(user),
        "can_ask": bool(can_ask or is_admin),
    }


async def _get_effective_db_config(user: User) -> tuple[Optional[UserDBConfig], Optional[str]]:
    config = await get_user_db_config_async(user.id)
    if config and config.is_active:
        return config, "user"
    config = await get_workspace_db_config_async(user.workspace_id)
    if config and config.is_active:
        return config, "workspace"
    if not _is_admin_user(user):
        config = await get_workspace_admin_db_config_async(user.workspace_id)
        if config and config.is_active:
            return config, "workspace"
    return None, None


async def _get_active_db_config_or_raise(user: User) -> tuple[UserDBConfig, str]:
    config, source = await _get_effective_db_config(user)
    if not config or not source:
        raise HTTPException(status_code=400, detail="请先连接数据库")
    return config, source


async def _load_user_role_ids(user: User) -> list[str]:
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(UserModel)
            .where(UserModel.id == user.id, UserModel.workspace_id == user.workspace_id)
            .options(selectinload(UserModel.roles).selectinload(RoleModel.permissions))
        )
        model = result.scalar_one_or_none()
        if not model:
            return []
        return [str(role.id) for role in model.roles]


async def _get_active_semantic_datasource(workspace_id: str) -> Optional[SemanticDatasourceModel]:
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(SemanticDatasourceModel)
            .where(
                SemanticDatasourceModel.workspace_id == workspace_id,
                SemanticDatasourceModel.is_active == True,  # noqa: E712
            )
            .order_by(SemanticDatasourceModel.updated_at.desc())
        )
        return result.scalars().first()


async def _get_member_semantic_schema(user: User) -> Optional[list[TableSchema]]:
    datasource = await _get_active_semantic_datasource(user.workspace_id)
    if not datasource:
        return None

    role_ids = await _load_user_role_ids(user)
    access_service = get_semantic_access_policy_service()
    user_access = {
        "user_id": str(user.id),
        "username": getattr(user, "username", None),
        "role_ids": role_ids,
        "is_admin": _is_admin_user(user),
    }
    user_access["semantic_access"] = await access_service.load_runtime_access(
        user.workspace_id,
        datasource.id,
        user_access,
    )
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        tables = list(
            (
                await session.execute(
                    select(SemanticTableModel).where(
                        SemanticTableModel.workspace_id == user.workspace_id,
                        SemanticTableModel.datasource_id == datasource.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        columns = list(
            (
                await session.execute(
                    select(SemanticColumnModel).where(
                        SemanticColumnModel.workspace_id == user.workspace_id,
                        SemanticColumnModel.datasource_id == datasource.id,
                    )
                )
            )
            .scalars()
            .all()
        )

    visible_tables = {
        table.id: table
        for table in tables
        if access_service.check_object_access("table", table, user_access).get("allowed")
    }
    columns_by_table: dict[int, list[dict]] = {}
    for column in columns:
        if column.table_id not in visible_tables:
            continue
        if not access_service.check_object_access("column", column, user_access).get("allowed"):
            continue
        columns_by_table.setdefault(column.table_id, []).append(
            {
                "name": column.physical_name,
                "type": column.data_type or "",
                "nullable": True,
                "primary_key": bool(column.is_primary_key),
                "business_name": column.business_name,
                "description": column.description or column.physical_comment,
                "is_sensitive": bool(column.is_sensitive),
                "queryable": bool(column.is_queryable),
            }
        )

    visible_schema = []
    for table in visible_tables.values():
        table_columns = sorted(
            columns_by_table.get(table.id, []),
            key=lambda item: next(
                (column.ordinal_position for column in columns if column.table_id == table.id and column.physical_name == item["name"]),
                0,
            ),
        )
        if not table_columns:
            continue
        visible_schema.append(
            TableSchema(
                name=table.physical_name,
                business_name=table.business_name,
                description=table.description or table.physical_comment,
                is_sensitive=bool(table.is_sensitive),
                queryable=bool(table.is_queryable),
                columns=table_columns,
            )
        )

    return sorted(visible_schema, key=lambda item: item.name.lower())


def build_connection_url(config: ConnectRequest) -> str:
    return (
        f"mysql+pymysql://{config.username}:{config.password}"
        f"@{config.host}:{config.port}/{config.database}"
    )


def get_table_list(engine) -> list[str]:
    inspector = inspect(engine)
    return inspector.get_table_names()


def get_table_schema(engine, table_name: str) -> TableSchema:
    inspector = inspect(engine)
    columns = []
    for col in inspector.get_columns(table_name):
        columns.append(
            {
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col.get("nullable", True),
                "primary_key": col.get("primary_key", False),
            }
        )

    # Row counts are intentionally omitted here. COUNT(*) can scan entire large
    # tables and made opening the database page block unrelated API requests.
    return TableSchema(name=table_name, columns=columns, row_count=None)


def _test_connection_sync(connection_url: str) -> None:
    engine = create_mysql_engine(connection_url, pool_pre_ping=True, pool_size=1)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def _load_table_names_sync(connection_url: str) -> list[str]:
    engine = create_mysql_engine(connection_url, pool_pre_ping=True, pool_size=1)
    try:
        return get_table_list(engine)
    finally:
        engine.dispose()


def _load_schema_sync(connection_url: str, limit: int = 50) -> tuple[list[str], list[TableSchema]]:
    engine = create_mysql_engine(connection_url, pool_pre_ping=True, pool_size=5)
    try:
        table_names = get_table_list(engine)
        schema = [get_table_schema(engine, name) for name in table_names[:limit]]
        return table_names, schema
    finally:
        engine.dispose()


def _quote_identifier(identifier: str) -> str:
    return f"`{str(identifier).replace('`', '``')}`"


def _sql_literal(value: object) -> str:
    text_value = str(value)
    return "'" + text_value.replace("\\", "\\\\").replace("'", "''") + "'"


async def _load_scope_dept_ids(user: User) -> list[int]:
    dept_id = user.department_id
    if not dept_id:
        return []
    if user.data_scope != 2:
        return [int(dept_id)]
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(DepartmentModel.id).where(
                DepartmentModel.workspace_id == user.workspace_id,
                DepartmentModel.status == True,  # noqa: E712
                (
                    (DepartmentModel.id == int(dept_id))
                    | (DepartmentModel.ancestors.like(f"%/{int(dept_id)}/%"))
                ),
            )
        )
        return sorted({int(item) for item in result.scalars().all()})


async def _build_row_scope_where_clause(user: User, table_name: str) -> str:
    if _is_admin_user(user) and not await authorization_v2_is_active():
        return ""

    datasource = await _get_active_semantic_datasource(user.workspace_id)
    if not datasource:
        raise HTTPException(status_code=403, detail="无可用语义模型，无法应用行级权限")

    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        table = (
            await session.execute(
                select(SemanticTableModel).where(
                    SemanticTableModel.workspace_id == user.workspace_id,
                    SemanticTableModel.datasource_id == datasource.id,
                    SemanticTableModel.physical_name == table_name,
                )
            )
        ).scalar_one_or_none()
        if not table:
            raise HTTPException(status_code=403, detail="无权访问该数据表")
        columns = list(
            (
                await session.execute(
                    select(SemanticColumnModel).where(
                        SemanticColumnModel.workspace_id == user.workspace_id,
                        SemanticColumnModel.datasource_id == datasource.id,
                        SemanticColumnModel.table_id == table.id,
                    )
                )
            ).scalars().all()
        )

    access_service = get_semantic_access_policy_service()
    user_access = {
        "user_id": str(user.id),
        "username": getattr(user, "username", None),
        "role_ids": await _load_user_role_ids(user),
        "is_admin": False,
        "dept_id": getattr(user, "department_id", None),
        "scope_dept_ids": await _load_scope_dept_ids(user),
    }
    user_access["semantic_access"] = await access_service.load_runtime_access(
        user.workspace_id,
        datasource.id,
        user_access,
    )
    if not access_service.check_object_access("table", table, user_access).get("allowed"):
        raise HTTPException(status_code=403, detail="无权访问该数据表")
    predicate = access_service.compile_table_predicate(table, columns, user_access, alias="")
    if predicate == "1=0":
        raise HTTPException(status_code=403, detail="当前权限没有可见数据行")
    return f" WHERE {predicate}" if predicate else ""


def _extract_sql_error_code(exc: SQLAlchemyError) -> Optional[int]:
    original = getattr(exc, "orig", None)
    args = getattr(original, "args", None)
    if isinstance(args, tuple) and args:
        first = args[0]
        if isinstance(first, int):
            return first
    return None


def classify_connect_error(exc: SQLAlchemyError, database_name: str) -> tuple[str, str]:
    error_code = _extract_sql_error_code(exc)
    raw_text = str(exc).lower()

    if error_code == 1049 or "unknown database" in raw_text:
        return DB_CONNECT_CODE_NOT_FOUND, f"目标数据库不存在：{database_name}"
    if error_code == 1045 or "access denied" in raw_text:
        return DB_CONNECT_CODE_AUTH_FAILED, "数据库账号或密码错误，请检查后重试。"
    if error_code in {2002, 2003, 2005} or "can't connect" in raw_text or "connection refused" in raw_text:
        return DB_CONNECT_CODE_HOST_UNREACHABLE, "数据库地址或端口不可达，请检查网络与服务状态。"
    return DB_CONNECT_CODE_UNKNOWN, "数据库连接失败，请检查参数后重试。"


def connect_error_response(code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "code": code,
            "message": message,
            "detail": message,
        },
    )


def whitelist_blocked_response(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "code": DBWhitelistService.BLOCKED_CODE,
            "message": DBWhitelistService.BLOCKED_MESSAGE,
            "detail": detail,
        },
    )


async def _ensure_whitelist_allowed(
    *,
    workspace_id: str,
    user_id: Optional[str],
    host: Optional[str],
    port: Optional[int],
    route: str,
) -> Optional[JSONResponse]:
    result = await _whitelist_service.check_endpoint_allowed(
        workspace_id,
        host=host,
        port=port,
    )
    if result.allowed:
        return None

    detail = await _whitelist_service.build_block_detail(
        workspace_id=workspace_id,
        user_id=user_id,
        host=host,
        port=port,
        route=route,
    )
    logger.warning(
        "[DBWhitelist] blocked workspace=%s user_id=%s route=%s host=%s port=%s detail=%s",
        workspace_id,
        user_id,
        route,
        host,
        port,
        result.detail,
    )
    return whitelist_blocked_response(detail)


async def _resolve_user_workspace_id(user_id: str) -> Optional[str]:
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        stmt = select(UserModel.workspace_id).where(UserModel.id == user_id)
        return (await session.execute(stmt)).scalar_one_or_none()


async def _get_active_user_db_config_or_raise(user_id: str) -> UserDBConfig:
    config = await get_user_db_config_async(user_id)
    if not config or not config.is_active:
        raise HTTPException(status_code=400, detail="请先连接数据库")
    return config


@router.post("/connect", response_model=ConnectResponse)
async def connect_database(request: ConnectRequest, current_user: User = Depends(get_current_user)):
    _require_admin_db_management(current_user)
    block = await _ensure_whitelist_allowed(
        workspace_id=current_user.workspace_id,
        user_id=current_user.id,
        host=request.host,
        port=request.port,
        route="/db/connect",
    )
    if block:
        return block

    try:
        url = build_connection_url(request)
        tables = await asyncio.to_thread(_load_table_names_sync, url)

        config = UserDBConfig(
            user_id=current_user.id,
            workspace_id=current_user.workspace_id,
            configured_by=current_user.id,
            host=request.host,
            port=request.port,
            username=request.username,
            encrypted_password=UserDBConfig.encrypt_password(request.password),
            database=request.database,
        )
        await save_user_db_config_async(config)
        if _is_admin_user(current_user):
            await save_workspace_db_config_async(
                current_user.workspace_id,
                config,
                current_user.id,
            )
        save_user_db_config(config)
        activate_user_connection(current_user.id)

        logger.info("User %s connected DB %s", current_user.id, request.database)
        return ConnectResponse(
            status="success",
            message=f"连接成功，发现 {len(tables)} 张表",
            tables=tables,
        )
    except SQLAlchemyError as exc:
        logger.error("Database connect failed: %s", exc)
        code, message = classify_connect_error(exc, request.database)
        return connect_error_response(code, message)


@router.post("/test")
async def test_database_connection(request: ConnectRequest, current_user: User = Depends(get_current_user)):
    _require_admin_db_management(current_user)
    block = await _ensure_whitelist_allowed(
        workspace_id=current_user.workspace_id,
        user_id=current_user.id,
        host=request.host,
        port=request.port,
        route="/db/test",
    )
    if block:
        return block

    try:
        await asyncio.to_thread(_test_connection_sync, build_connection_url(request))
        return {"status": "success", "message": "测试连接成功"}
    except SQLAlchemyError as exc:
        code, message = classify_connect_error(exc, request.database)
        return connect_error_response(code, message)


@router.post("/disconnect")
async def disconnect_database(current_user: User = Depends(get_current_user)):
    _require_admin_db_management(current_user)
    await disconnect_user_async(current_user.id)
    if _is_admin_user(current_user):
        await disconnect_workspace_async(current_user.workspace_id)
    disconnect_user(current_user.id)
    logger.info("User %s disconnected DB", current_user.id)
    return {"status": "success", "message": "已断开连接"}


@router.get("/status", response_model=DBConnectionStatus)
async def get_connection_status(current_user: User = Depends(get_current_user)):
    capabilities = _build_capabilities(current_user)
    v2_active = await authorization_v2_is_active()
    if v2_active:
        capabilities["can_query_sql"] = False
    config, source = await _get_effective_db_config(current_user)
    if not config or not source:
        return DBConnectionStatus(is_connected=False, **capabilities)

    block = await _ensure_whitelist_allowed(
        workspace_id=current_user.workspace_id,
        user_id=current_user.id,
        host=config.host,
        port=config.port,
        route="/db/status",
    )
    if block:
        logger.warning(
            "[DBWhitelist] status blocked workspace=%s user_id=%s host=%s port=%s",
            current_user.workspace_id,
            current_user.id,
            config.host,
            config.port,
        )
        # Keep status endpoint backward compatible: treat blocked endpoint as disconnected.
        return DBConnectionStatus(is_connected=False, **capabilities)

    # Keep status checks fast. Full schema discovery belongs to /db/schema and
    # may take a long time for large external databases.
    tables = []
    if not _is_admin_user(current_user):
        try:
            semantic_schema = await _get_member_semantic_schema(current_user)
            if semantic_schema:
                tables = [table.name for table in semantic_schema]
        except Exception:
            pass

    return DBConnectionStatus(
        is_connected=True,
        host=config.host,
        database=config.database,
        tables=tables,
        source=source,
        **{
            **_build_capabilities(current_user, can_ask=bool(tables)),
            **({"can_query_sql": False} if v2_active else {}),
        },
    )


@router.get("/schema", response_model=SchemaResponse)
async def get_database_schema(current_user: User = Depends(get_current_user)):
    config, source = await _get_active_db_config_or_raise(current_user)

    block = await _ensure_whitelist_allowed(
        workspace_id=current_user.workspace_id,
        user_id=current_user.id,
        host=config.host,
        port=config.port,
        route="/db/schema",
    )
    if block:
        return block

    try:
        if _is_admin_user(current_user) and not await authorization_v2_is_active():
            table_names, tables = await asyncio.to_thread(_load_schema_sync, config.get_connection_url())
        else:
            semantic_schema = await _get_member_semantic_schema(current_user)
            if semantic_schema:
                tables = semantic_schema[:50]
                table_names = [table.name for table in semantic_schema]
            else:
                tables = []
                table_names = []
        return SchemaResponse(database=config.database, tables=tables, table_names=table_names)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取 Schema 失败: {str(e)}")


@router.get("/tables")
async def get_tables(current_user: User = Depends(get_current_user)):
    config, _source = await _get_effective_db_config(current_user)
    if not config:
        return {"tables": [], "connected": False}

    block = await _ensure_whitelist_allowed(
        workspace_id=current_user.workspace_id,
        user_id=current_user.id,
        host=config.host,
        port=config.port,
        route="/db/tables",
    )
    if block:
        return block

    try:
        if _is_admin_user(current_user) and not await authorization_v2_is_active():
            tables = await asyncio.to_thread(_load_table_names_sync, config.get_connection_url())
        else:
            semantic_schema = await _get_member_semantic_schema(current_user)
            if semantic_schema:
                tables = [table.name for table in semantic_schema]
        return {"tables": tables, "connected": True}
    except Exception:
        return {"tables": [], "connected": False}


async def get_user_engine(user_id: str):
    """Get SQLAlchemy engine for SQL worker with whitelist enforcement."""

    try:
        config = await get_user_db_config_async(user_id)
        workspace_id = await _resolve_user_workspace_id(user_id)

        # fallback to workspace-level shared config
        if (not config or not config.is_active) and workspace_id:
            config = await get_workspace_db_config_async(workspace_id)
        if (not config or not config.is_active) and workspace_id:
            config = await get_workspace_admin_db_config_async(workspace_id)

        if not config or not config.is_active:
            logger.warning("get_user_engine: user %s has no active DB config", user_id)
            return None

        block = await _ensure_whitelist_allowed(
            workspace_id=workspace_id or "",
            user_id=user_id,
            host=config.host,
            port=config.port,
            route="get_user_engine",
        )
        if block:
            raise PermissionError(DBWhitelistService.BLOCKED_CODE)

        connection_url = config.get_connection_url()
        return create_mysql_engine(
            connection_url,
            pool_pre_ping=True,
            pool_size=5,
        )
    except PermissionError:
        raise
    except Exception as e:
        logger.error("get_user_engine failed: %s", e)
        raise


@router.get("/tables/{table_name}/description")
async def get_table_description(table_name: str, current_user: User = Depends(get_current_user)):
    user_descs = _table_descriptions.get(current_user.id, {})
    return {"table_name": table_name, "description": user_descs.get(table_name, "")}


@router.post("/tables/{table_name}/description")
async def save_table_description(
    table_name: str,
    request: TableDescriptionRequest,
    current_user: User = Depends(get_current_user),
):
    if current_user.id not in _table_descriptions:
        _table_descriptions[current_user.id] = {}

    _table_descriptions[current_user.id][table_name] = request.description
    logger.info("User %s updated table description: %s", current_user.id, table_name)
    return {"status": "success", "message": "描述已保存"}


@router.get("/tables/descriptions")
async def get_all_table_descriptions(current_user: User = Depends(get_current_user)):
    return {"descriptions": _table_descriptions.get(current_user.id, {})}


@router.post("/readonly-config")
async def configure_readonly_account(
    request: ReadonlyConfigRequest,
    current_user: User = Depends(get_current_user),
):
    _require_admin_db_management(current_user)
    config, source = await _get_active_db_config_or_raise(current_user)

    block = await _ensure_whitelist_allowed(
        workspace_id=current_user.workspace_id,
        user_id=current_user.id,
        host=config.host,
        port=config.port,
        route="/db/readonly-config",
    )
    if block:
        return block

    try:
        readonly_url = (
            f"mysql+pymysql://{request.readonly_username}:{request.readonly_password}"
            f"@{config.host}:{config.port}/{config.database}"
        )
        engine = create_mysql_engine(
            readonly_url,
            pool_pre_ping=True,
            pool_size=1,
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=400, detail=f"只读账号连接失败: {str(e)}")

    config.readonly_username = request.readonly_username
    config.readonly_encrypted_password = UserDBConfig.encrypt_password(request.readonly_password)
    if source == "workspace" or _is_admin_user(current_user):
        await save_workspace_db_config_async(current_user.workspace_id, config, current_user.id)
    else:
        await save_user_db_config_async(config)

    logger.info("User %s configured readonly account", current_user.id)
    return {"status": "success", "message": "只读账号配置成功"}


@router.get("/tables/{table_name}/data", response_model=TableDataResponse)
async def get_table_data(
    table_name: str,
    page: int = 1,
    page_size: int = 50,
    sort_by: Optional[str] = None,
    sort_order: str = "DESC",
    current_user: User = Depends(get_current_user),
):
    config, _source = await _get_active_db_config_or_raise(current_user)

    block = await _ensure_whitelist_allowed(
        workspace_id=current_user.workspace_id,
        user_id=current_user.id,
        host=config.host,
        port=config.port,
        route="/db/tables/{table_name}/data",
    )
    if block:
        return block

    import re

    if not re.match(r"^[\u4e00-\u9fa5a-zA-Z_][\u4e00-\u9fa5a-zA-Z0-9_]*$", table_name):
        raise HTTPException(status_code=400, detail="无效的表名")

    connection_url = config.get_readonly_connection_url() or config.get_connection_url()

    try:
        from app.core.db.read_only_executor import ReadOnlyExecutor

        executor = ReadOnlyExecutor(current_user.id, connection_url)

        if _is_admin_user(current_user) and not await authorization_v2_is_active():
            total_count = await asyncio.to_thread(executor.get_table_count, table_name)
            result = await asyncio.to_thread(
                executor.get_table_data,
                table_name,
                page,
                page_size,
                sort_by,
                sort_order,
            )
        else:
            semantic_schema = await _get_member_semantic_schema(current_user)
            if semantic_schema:
                table_schema = next(
                    (item for item in semantic_schema if item.name.lower() == table_name.lower()),
                    None,
                )
                if not table_schema:
                    raise HTTPException(status_code=403, detail="无权访问该数据表")
                allowed_columns = [column["name"] for column in table_schema.columns]
            else:
                raise HTTPException(status_code=403, detail="无可用语义模型，无法预览数据表")

            if not allowed_columns:
                raise HTTPException(status_code=403, detail="无可预览字段")
            allowed_column_set = {column.lower() for column in allowed_columns}
            if sort_by and sort_by.lower() not in allowed_column_set:
                raise HTTPException(status_code=403, detail="无权按该字段排序")

            row_scope_where = await _build_row_scope_where_clause(current_user, table_name)
            offset = max(page - 1, 0) * page_size
            projection = ", ".join(_quote_identifier(column) for column in allowed_columns)
            order_clause = ""
            if sort_by:
                order = "DESC" if sort_order.upper() == "DESC" else "ASC"
                order_clause = f" ORDER BY {_quote_identifier(sort_by)} {order}"
            sql = (
                f"SELECT {projection} FROM {_quote_identifier(table_name)}"
                f"{row_scope_where}{order_clause} LIMIT {int(page_size)} OFFSET {int(offset)}"
            )
            count_result = await asyncio.to_thread(
                executor.execute_query,
                f"SELECT COUNT(*) AS cnt FROM {_quote_identifier(table_name)}{row_scope_where}",
                5,
                1,
            )
            if count_result.error:
                raise HTTPException(status_code=400, detail=count_result.error)
            total_count = int(count_result.rows[0][0]) if count_result.rows else 0
            result = await asyncio.to_thread(
                executor.execute_query,
                sql,
                5,
                max(int(page_size), 1),
            )

        if result.error:
            raise HTTPException(status_code=400, detail=result.error)

        return TableDataResponse(
            table_name=table_name,
            columns=result.columns,
            rows=[list(row) for row in result.rows],
            total_count=total_count,
            page=page,
            page_size=page_size,
            has_more=(page * page_size) < total_count,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get table data failed: %s", e)
        raise HTTPException(status_code=500, detail=f"获取表数据失败: {str(e)}")


@router.post("/query", response_model=QueryResponse)
async def execute_readonly_query(request: QueryRequest, current_user: User = Depends(get_current_user)):
    if not _can_query_sql(current_user):
        raise HTTPException(status_code=403, detail="当前用户无权使用手写 SQL 查询")
    if await authorization_v2_is_active():
        raise HTTPException(
            status_code=410,
            detail={
                "code": "manual_sql_retired",
                "message": "组织权限模型启用后，手写 SQL 不能绕过语义数据权限，请使用问数入口。",
                "replacement": "/api/chat",
            },
        )

    config, _source = await _get_active_db_config_or_raise(current_user)

    block = await _ensure_whitelist_allowed(
        workspace_id=current_user.workspace_id,
        user_id=current_user.id,
        host=config.host,
        port=config.port,
        route="/db/query",
    )
    if block:
        return block

    connection_url = config.get_readonly_connection_url() or config.get_connection_url()

    if not config.has_readonly_config():
        logger.warning("User %s runs query without readonly account", current_user.id)

    try:
        from app.core.db.read_only_executor import ReadOnlyExecutor

        executor = ReadOnlyExecutor(current_user.id, connection_url)
        result = await asyncio.to_thread(
            executor.execute_query,
            request.sql,
            request.timeout_sec,
            request.max_rows,
        )

        return QueryResponse(
            columns=result.columns,
            rows=[list(row) for row in result.rows],
            row_count=result.row_count,
            truncated=result.truncated,
            execution_time_ms=result.execution_time_ms,
            error=result.error,
        )
    except Exception as e:
        logger.error("SQL query failed: %s", e)
        raise HTTPException(status_code=500, detail=f"查询执行失败: {str(e)}")
