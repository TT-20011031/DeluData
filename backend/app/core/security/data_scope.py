"""
统一的数据范围权限策略模块。

该文件负责沉淀 RAG 检索与文件树查询共用的可见性规则，避免在多个服务中重复编写
ALL、DEPT_TREE、DEPT_ONLY、PERSONAL 等分支逻辑。模块输出可直接用于 Chroma 过滤、
SQLAlchemy 条件拼装以及检索结果二次鉴权判断，确保权限语义一致、扩展点集中、后续
新增隔离策略时只需修改一个位置，降低越权与回归风险。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional, Sequence

from sqlalchemy import and_, or_

logger = logging.getLogger(__name__)

_VALID_SCOPES = {
    1,  # 全部可见
    2,  # 本部门及下级
    3,  # 仅本部门
    4,  # 仅本人
}
_DEFAULT_VISIBILITIES = ("public", "dept", "private")


class DataScopeLevel:
    ALL = 1
    DEPT_TREE = 2
    DEPT_ONLY = 3
    PERSONAL = 4


@dataclass(frozen=True)
class LegacyScopeSpec:
    valid: bool
    apply_filter: bool
    visibilities: tuple[str, ...] = ()
    dept_ids: tuple[int, ...] = ()


def resolve_data_scope(value: Any) -> int:
    try:
        scope = int(value) if value is not None else DataScopeLevel.PERSONAL
    except (TypeError, ValueError):
        return DataScopeLevel.PERSONAL
    return scope if scope in _VALID_SCOPES else DataScopeLevel.PERSONAL


def parse_dept_id(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_dept_ids(values: Optional[Iterable[Any]]) -> list[int]:
    if not values:
        return []
    normalized: list[int] = []
    for value in values:
        dept_id = parse_dept_id(value)
        if dept_id is None:
            continue
        normalized.append(dept_id)
    return sorted(set(normalized))


def normalize_visibility(value: Any, default: str = "dept") -> str:
    vis = str(value or default).strip().lower()
    if vis == "workspace":
        return "dept"
    if vis in {"public", "dept", "private"}:
        return vis
    return default


def parse_legacy_scope(scope: Optional[str]) -> LegacyScopeSpec:
    """
    解析历史接口中的 `scope` 字符串参数。

    支持格式：
    - `None` 或空字符串：不额外附加可见性过滤
    - `all` 或 `*`
    - `public`
    - `private`
    - `dept_<id>`
    """
    if scope is None:
        return LegacyScopeSpec(valid=True, apply_filter=False)

    raw = str(scope).strip().lower()
    if not raw:
        return LegacyScopeSpec(valid=True, apply_filter=False)

    if raw in {"all", "*"}:
        return LegacyScopeSpec(valid=True, apply_filter=False)

    if raw == "public":
        return LegacyScopeSpec(valid=True, apply_filter=True, visibilities=("public",))

    if raw == "private":
        return LegacyScopeSpec(valid=True, apply_filter=True, visibilities=("private",))

    if raw.startswith("dept_"):
        dept_id = parse_dept_id(raw.removeprefix("dept_"))
        if dept_id is None:
            return LegacyScopeSpec(valid=False, apply_filter=False)
        return LegacyScopeSpec(
            valid=True,
            apply_filter=True,
            visibilities=("dept",),
            dept_ids=(dept_id,),
        )

    return LegacyScopeSpec(valid=False, apply_filter=False)


async def resolve_scope_dept_ids(
    user_context: Any,
    descendants_loader: Optional[Callable[[int], Awaitable[Sequence[int]]]] = None,
) -> list[int]:
    """解析当前用户在 DataScope 下可访问的部门 ID 列表。"""
    scope = resolve_data_scope(getattr(user_context, "data_scope", None))
    dept_id = parse_dept_id(getattr(user_context, "dept_id", None))
    if dept_id is None:
        return []

    if scope == DataScopeLevel.DEPT_ONLY:
        return [dept_id]

    if scope == DataScopeLevel.DEPT_TREE:
        allowed = {dept_id}
        if descendants_loader is not None:
            try:
                descendants = await descendants_loader(dept_id)
                for item in descendants:
                    parsed = parse_dept_id(item)
                    if parsed is not None:
                        allowed.add(parsed)
            except Exception as exc:
                logger.warning("[DataScope] 解析部门下级失败: %s", exc)
        return sorted(allowed)

    return []


def build_chroma_permission_filter(
    *,
    workspace_id: str,
    user_id: str,
    scope: int,
    scope_dept_ids: Optional[Sequence[int]] = None,
    is_workspace_admin: bool = False,
) -> dict[str, Any]:
    """构建 Chroma 查询条件：工作空间 + 可见性 + 数据范围。"""
    workspace_filter = {"workspace_id": {"$eq": workspace_id or "default"}}
    if is_workspace_admin:
        return workspace_filter
    or_clauses: list[dict[str, Any]] = [
        {"visibility": {"$eq": "public"}},
        {"owner_id": {"$eq": user_id}},
    ]
    dept_visibility_filter = {"visibility": {"$in": ["dept", "workspace"]}}

    if scope == DataScopeLevel.ALL:
        or_clauses.append(dept_visibility_filter)
    elif scope in (DataScopeLevel.DEPT_ONLY, DataScopeLevel.DEPT_TREE):
        dept_ids = [str(item) for item in (scope_dept_ids or [])]
        if dept_ids:
            or_clauses.append(
                {
                    "$and": [
                        dept_visibility_filter,
                        {"dept_id": {"$in": dept_ids}},
                    ]
                }
            )

    return {"$and": [workspace_filter, {"$or": or_clauses}]}


def can_access_metadata(
    metadata: Mapping[str, Any],
    user_context: Any,
    allowed_dept_ids: Optional[Sequence[Any]] = None,
) -> bool:
    """对单条元数据执行可见性校验（用于检索结果二次过滤）。"""
    meta = metadata or {}
    if bool(getattr(user_context, "is_workspace_admin", False)):
        metadata_workspace_id = str(meta.get("workspace_id") or "")
        expected_workspace_id = str(getattr(user_context, "workspace_id", "") or "")
        return not metadata_workspace_id or metadata_workspace_id == expected_workspace_id
    visibility = normalize_visibility(meta.get("visibility"), default="dept")
    owner_id = str(meta.get("owner_id") or "")
    dept_id = str(meta.get("dept_id") or "")

    user_id = str(getattr(user_context, "user_id", "") or "")
    scope = resolve_data_scope(getattr(user_context, "data_scope", None))
    allowed_set = {str(item) for item in (allowed_dept_ids or []) if str(item)}

    if visibility == "public":
        return True

    if visibility == "private":
        return owner_id == user_id

    if visibility == "dept":
        if owner_id == user_id:
            return True
        if scope == DataScopeLevel.ALL:
            return (not allowed_set) or (dept_id in allowed_set)
        if scope in (DataScopeLevel.DEPT_ONLY, DataScopeLevel.DEPT_TREE):
            return dept_id in allowed_set
        return False

    return False


def build_visibility_where_clause(
    model: Any,
    *,
    user_context: Any,
    scope_dept_ids: Optional[Sequence[int]] = None,
    visibilities: Optional[Iterable[Any]] = None,
    dept_ids: Optional[Iterable[Any]] = None,
    allow_private_without_owner: bool = False,
):
    """构建 SQLAlchemy 可见性条件（适用于 File/Folder 查询）。"""
    allowed_vis = {
        normalize_visibility(value, default="")
        for value in (visibilities or _DEFAULT_VISIBILITIES)
        if str(value).strip()
    }
    user_id = str(getattr(user_context, "user_id", "") or "")
    is_workspace_admin = bool(getattr(user_context, "is_workspace_admin", False))
    scope = resolve_data_scope(getattr(user_context, "data_scope", None))
    requested_dept_ids = set(normalize_dept_ids(dept_ids))
    scope_dept_set = set(scope_dept_ids or [])

    clauses = []

    if "public" in allowed_vis:
        clauses.append(model.visibility == "public")

    if "private" in allowed_vis:
        if is_workspace_admin:
            clauses.append(model.visibility == "private")
        elif user_id:
            clauses.append(and_(model.visibility == "private", model.owner_id == user_id))
        elif allow_private_without_owner:
            clauses.append(model.visibility == "private")

    if "dept" in allowed_vis:
        dept_clause = _build_dept_clause(
            model=model,
            user_id=user_id,
            scope=scope,
            scope_dept_ids=scope_dept_set,
            requested_dept_ids=requested_dept_ids,
        )
        if dept_clause is not None:
            clauses.append(dept_clause)

    if not clauses:
        return None
    return or_(*clauses)


def _build_dept_clause(
    *,
    model: Any,
    user_id: str,
    scope: int,
    scope_dept_ids: set[int],
    requested_dept_ids: set[int],
):
    owner_clause = None
    if user_id:
        owner_clause = and_(model.visibility == "dept", model.owner_id == user_id)
        # 显式指定部门时，owner 分支也必须收敛到请求部门，避免“选了部门仍看到全部”。
        if requested_dept_ids:
            owner_clause = and_(
                owner_clause,
                model.dept_id.in_(sorted(requested_dept_ids)),
            )

    if scope == DataScopeLevel.ALL:
        if requested_dept_ids:
            dept_clause = and_(
                model.visibility == "dept",
                model.dept_id.in_(sorted(requested_dept_ids)),
            )
        else:
            dept_clause = model.visibility == "dept"
        return or_(owner_clause, dept_clause) if owner_clause is not None else dept_clause

    if scope == DataScopeLevel.PERSONAL:
        if requested_dept_ids and not user_id:
            return None
        return owner_clause

    if scope in (DataScopeLevel.DEPT_ONLY, DataScopeLevel.DEPT_TREE):
        effective_dept_ids = set(scope_dept_ids)
        if requested_dept_ids:
            effective_dept_ids &= requested_dept_ids

        if effective_dept_ids:
            dept_clause = and_(
                model.visibility == "dept",
                model.dept_id.in_(sorted(effective_dept_ids)),
            )
            return or_(owner_clause, dept_clause) if owner_clause is not None else dept_clause

        if requested_dept_ids and user_id:
            return owner_clause
        return owner_clause

    return owner_clause
