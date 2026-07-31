"""Canonical tenant capability catalogue and legacy aliases."""

from __future__ import annotations

from typing import Iterable


AUTHORIZATION_V2_MIGRATION_ID = "2026_07_authorization_v2"


CAPABILITIES: dict[str, tuple[str, str]] = {
    "database:manage": ("数据库", "管理数据源与语义模型"),
    "chat:use": ("对话", "使用 AI 对话"),
    "knowledge:view": ("知识库", "查看知识库"),
    "knowledge:manage": ("知识库", "管理知识库"),
    "database:view": ("数据库", "查看数据库配置"),
    "database:query": ("数据库", "执行只读查询"),
    "user:view": ("组织权限", "查看人员与任职"),
    "user:manage": ("组织权限", "管理人员与任职"),
    "org:view": ("组织权限", "查看组织与岗位"),
    "org:manage": ("组织权限", "管理组织与岗位"),
    "role:view": ("组织权限", "查看角色与授权"),
    "role:manage": ("组织权限", "管理角色"),
    "authorization:view": ("组织权限", "查看有效权限与审计"),
    "authorization:manage": ("组织权限", "管理角色授权"),
    "authorization:delegate": ("组织权限", "创建受控用户例外"),
    "config:view": ("配置", "查看租户配置"),
    "config:manage": ("配置", "管理租户配置"),
    "semantic_access:view": ("问数权限", "查看问数数据权限"),
    "semantic_access:manage": ("问数权限", "保存并发布问数数据权限"),
    "museum:guide": ("博物馆", "使用智能导览"),
    "museum:shop": ("博物馆", "访问商店"),
    "museum:manage": ("博物馆", "管理博物馆内容"),
}


LEGACY_CAPABILITY_ALIASES: dict[str, str] = {
    "sql:query": "database:query",
    "db:manage": "database:manage",
    "kb:read": "knowledge:view",
    "kb:manage": "knowledge:manage",
    "museum:product_manage": "museum:manage",
    "sys:user:view": "user:view",
    "sys:user:edit": "user:manage",
    "sys:role:view": "role:view",
    "sys:role:edit": "role:manage",
    "dept:view": "org:view",
    "dept:manage": "org:manage",
    "sql:manage": "semantic_access:manage",
}


def canonical_capability(code: str) -> str:
    return LEGACY_CAPABILITY_ALIASES.get(code, code)


def canonicalize_capabilities(codes: Iterable[str]) -> set[str]:
    result = {canonical_capability(code) for code in codes}
    if "*" in result:
        result.update(CAPABILITIES)
    return result


def is_registered_capability(code: str) -> bool:
    return canonical_capability(code) in CAPABILITIES or code == "*"
