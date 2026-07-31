import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

import pytest

from app.core.security.data_scope import (
    build_visibility_where_clause,
    build_chroma_permission_filter,
    can_access_metadata,
    parse_legacy_scope,
    resolve_data_scope,
    resolve_scope_dept_ids,
)
from app.models.auth.organization import DataScope
from app.models.knowledge.graph import File


def _ctx(
    *,
    user_id: str = "u1",
    workspace_id: str = "ws1",
    data_scope: int = DataScope.PERSONAL,
    dept_id: int | None = None,
    is_workspace_admin: bool = False,
):
    return SimpleNamespace(
        user_id=user_id,
        workspace_id=workspace_id,
        data_scope=data_scope,
        dept_id=dept_id,
        is_workspace_admin=is_workspace_admin,
    )


def test_resolve_data_scope_defaults_to_personal_on_invalid():
    assert resolve_data_scope("invalid") == DataScope.PERSONAL
    assert resolve_data_scope(999) == DataScope.PERSONAL
    assert resolve_data_scope(DataScope.ALL) == DataScope.ALL


@pytest.mark.asyncio
async def test_resolve_scope_dept_ids_for_dept_tree():
    async def _descendants(_dept_id: int):
        return [11, 12]

    ctx = _ctx(data_scope=DataScope.DEPT_TREE, dept_id=10)
    dept_ids = await resolve_scope_dept_ids(ctx, descendants_loader=_descendants)

    assert dept_ids == [10, 11, 12]


def test_build_chroma_permission_filter_for_personal():
    where_filter = build_chroma_permission_filter(
        workspace_id="ws1",
        user_id="u1",
        scope=DataScope.PERSONAL,
        scope_dept_ids=[],
    )
    assert "$and" in where_filter
    assert where_filter["$and"][0] == {"workspace_id": {"$eq": "ws1"}}
    assert {"visibility": {"$eq": "public"}} in where_filter["$and"][1]["$or"]
    assert {"owner_id": {"$eq": "u1"}} in where_filter["$and"][1]["$or"]


def test_build_chroma_permission_filter_keeps_legacy_workspace_visibility():
    where_filter = build_chroma_permission_filter(
        workspace_id="ws1",
        user_id="u1",
        scope=DataScope.ALL,
        scope_dept_ids=[],
    )
    visibility_or = where_filter["$and"][1]["$or"]

    assert {"visibility": {"$in": ["dept", "workspace"]}} in visibility_or

    dept_filter = build_chroma_permission_filter(
        workspace_id="ws1",
        user_id="u1",
        scope=DataScope.DEPT_ONLY,
        scope_dept_ids=[10],
    )
    dept_or = dept_filter["$and"][1]["$or"]

    assert {
        "$and": [
            {"visibility": {"$in": ["dept", "workspace"]}},
            {"dept_id": {"$in": ["10"]}},
        ]
    } in dept_or


def test_workspace_admin_chroma_filter_only_keeps_workspace_boundary():
    assert build_chroma_permission_filter(
        workspace_id="ws1",
        user_id="owner",
        scope=DataScope.ALL,
        scope_dept_ids=[],
        is_workspace_admin=True,
    ) == {"workspace_id": {"$eq": "ws1"}}


def test_can_access_metadata_matrix():
    ctx_all = _ctx(data_scope=DataScope.ALL)
    ctx_personal = _ctx(data_scope=DataScope.PERSONAL)
    ctx_dept = _ctx(data_scope=DataScope.DEPT_ONLY, dept_id=10)

    assert can_access_metadata({"visibility": "public"}, ctx_personal, [])
    assert can_access_metadata({"visibility": "private", "owner_id": "u1"}, ctx_personal, [])
    assert not can_access_metadata({"visibility": "private", "owner_id": "u2"}, ctx_personal, [])

    assert can_access_metadata(
        {"visibility": "dept", "dept_id": "10", "owner_id": "u2"},
        ctx_all,
        [],
    )
    assert can_access_metadata(
        {"visibility": "workspace", "dept_id": "10", "owner_id": "u2"},
        ctx_dept,
        ["10"],
    )
    assert not can_access_metadata(
        {"visibility": "dept", "dept_id": "11", "owner_id": "u2"},
        ctx_dept,
        ["10"],
    )


def test_workspace_admin_can_read_private_metadata_only_in_own_workspace():
    ctx = _ctx(user_id="owner", is_workspace_admin=True)
    assert can_access_metadata(
        {"workspace_id": "ws1", "visibility": "private", "owner_id": "u2"},
        ctx,
        [],
    )
    assert not can_access_metadata(
        {"workspace_id": "ws2", "visibility": "public"},
        ctx,
        [],
    )


def test_parse_legacy_scope_matrix():
    assert parse_legacy_scope(None).valid is True
    assert parse_legacy_scope(None).apply_filter is False

    public_scope = parse_legacy_scope("public")
    assert public_scope.valid is True
    assert public_scope.visibilities == ("public",)

    private_scope = parse_legacy_scope("private")
    assert private_scope.valid is True
    assert private_scope.visibilities == ("private",)

    dept_scope = parse_legacy_scope("dept_12")
    assert dept_scope.valid is True
    assert dept_scope.visibilities == ("dept",)
    assert dept_scope.dept_ids == (12,)

    assert parse_legacy_scope("dept_x").valid is False
    assert parse_legacy_scope("unknown").valid is False


def test_build_visibility_clause_private_without_user_for_legacy_scope():
    legacy_ctx = _ctx(user_id="", data_scope=DataScope.ALL)
    clause = build_visibility_where_clause(
        File,
        user_context=legacy_ctx,
        visibilities=("private",),
        allow_private_without_owner=True,
    )
    sql_text = str(clause)
    assert "files.visibility" in sql_text


def test_build_visibility_clause_requested_dept_also_limits_owner_branch():
    ctx = _ctx(user_id="u1", data_scope=DataScope.ALL)
    clause = build_visibility_where_clause(
        File,
        user_context=ctx,
        visibilities=("dept",),
        dept_ids=[9],
    )
    sql_text = str(clause)
    # 选中具体部门时，owner 分支必须同时带上 dept_id 过滤条件。
    assert "files.owner_id = :owner_id_1 AND files.dept_id IN" in sql_text

    params = clause.compile().params
    dept_params = [value for key, value in params.items() if key.startswith("dept_id_")]
    assert dept_params
    for value in dept_params:
        assert isinstance(value, list)
        assert all(isinstance(item, int) for item in value)


def test_workspace_admin_private_visibility_clause_does_not_filter_owner():
    ctx = _ctx(user_id="owner", data_scope=DataScope.ALL, is_workspace_admin=True)
    clause = build_visibility_where_clause(
        File,
        user_context=ctx,
        visibilities=("private",),
    )
    sql_text = str(clause)
    assert "files.visibility" in sql_text
    assert "files.owner_id" not in sql_text
