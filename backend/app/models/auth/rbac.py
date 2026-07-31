"""
DeluData 智能问数系统 - RBAC 权限模型

标准5表设计：Users, Roles, Permissions, User_Roles, Role_Permissions
支持租户隔离 + 系统预设角色 + 自定义角色
"""
from typing import Set, List, Optional
from sqlalchemy import Column, String, Integer, Boolean, ForeignKey, Table, UniqueConstraint, Text
from sqlalchemy.orm import relationship, Mapped

from app.core.db.database import Base


# ========== 关联表 (Junction Tables) ==========

# 用户-角色 关联表
user_roles = Table(
    'sys_user_roles',
    Base.metadata,
    Column('user_id', String(36), ForeignKey('sys_users.id', ondelete='CASCADE'), primary_key=True),
    Column('role_id', Integer, ForeignKey('sys_roles.id', ondelete='CASCADE'), primary_key=True)
)

# 角色-权限 关联表
role_permissions = Table(
    'sys_role_permissions',
    Base.metadata,
    Column('role_id', Integer, ForeignKey('sys_roles.id', ondelete='CASCADE'), primary_key=True),
    Column('permission_id', Integer, ForeignKey('sys_permissions.id', ondelete='CASCADE'), primary_key=True)
)


# ========== 权限表 (Permission) ==========

class PermissionModel(Base):
    """
    原子权限表 (系统硬编码同步到数据库)
    
    权限码格式: {module}:{action}
    例如: sql:query, kb:manage, file:read
    """
    __tablename__ = 'sys_permissions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(64), unique=True, nullable=False, index=True)  # e.g., "sql:query"
    module = Column(String(32), nullable=False, index=True)  # e.g., "SQL"
    description = Column(String(128))
    
    # 关系: 一个权限可以属于多个角色
    roles = relationship('RoleModel', secondary=role_permissions, back_populates='permissions')
    
    def __repr__(self):
        return f"<Permission {self.code}>"


# ========== 角色表 (Role) ==========

class RoleModel(Base):
    """
    角色表 (支持系统预设 + 用户自定义)
    
    workspace_id:
    - NULL: 系统全局角色，所有租户可见
    - 有值: 租户自定义角色，仅该租户可见
    """
    __tablename__ = 'sys_roles'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False)
    description = Column(String(255))
    workspace_id = Column(String(36), nullable=True, index=True)  # NULL=系统全局, 有值=租户自定义
    is_system = Column(Boolean, default=False)  # 系统预设不可删除
    
    # 数据范围 (1=全部, 2=本部门及以下, 3=本部门, 4=仅本人)
    data_scope = Column(Integer, default=4)
    
    # 关系
    users = relationship('UserModel', secondary=user_roles, back_populates='roles')
    permissions = relationship('PermissionModel', secondary=role_permissions, back_populates='roles', lazy='selectin')
    
    __table_args__ = (
        # 同一个 workspace 下角色名不能重复
        UniqueConstraint('workspace_id', 'name', name='uq_workspace_role_name'),
    )
    
    def __repr__(self):
        return f"<Role {self.name}>"
    
    def get_permission_codes(self) -> Set[str]:
        """获取该角色的所有权限码"""
        return {perm.code for perm in self.permissions}


# ========== 用户表 (User) ==========

class UserModel(Base):
    """
    用户表 (纯 RBAC 版)
    
    支持多角色、租户隔离
    权限完全来自角色，无上帝账号字段
    """
    __tablename__ = 'sys_users'
    
    id = Column(String(36), primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(128), nullable=True)
    hashed_password = Column(String(128), nullable=False)
    workspace_id = Column(String(36), nullable=False, index=True, default='default')
    disabled = Column(Boolean, default=False)
    
    # 部门关联 (数据权限隔离)
    department_id = Column(Integer, ForeignKey('sys_departments.id'), nullable=True)
    
    # 关系: 用户的角色列表 (使用 selectin 加载适合异步)
    roles = relationship('RoleModel', secondary=user_roles, back_populates='users', lazy='selectin')
    
    def __repr__(self):
        return f"<User {self.username}>"
    
    @property
    def all_permissions(self) -> Set[str]:
        """
        获取用户当前所有权限 Code 集合
        
        纯 RBAC：遍历所有角色，合并权限
        如果某角色拥有 * 权限，则包含在内
        """
        perms = set()
        for role in self.roles:
            for perm in role.permissions:
                perms.add(perm.code)
        return perms
    
    @property
    def is_admin(self) -> bool:
        """
        检查用户是否为管理员（拥有 * 权限）
        
        纯 RBAC：通过权限判断，而非特殊字段
        """
        return "*" in self.all_permissions
    
    def has_perm(self, perm_code: str) -> bool:
        """
        检查用户是否拥有指定权限
        
        纯 RBAC 逻辑：
        1. 如果拥有 * 权限（超级管理员角色），返回 True
        2. 否则检查具体权限码
        """
        perms = self.all_permissions
        if "*" in perms:
            return True
        return perm_code in perms
    
    def has_any_perm(self, perm_codes: List[str]) -> bool:
        """检查用户是否拥有任意一个权限"""
        perms = self.all_permissions
        if "*" in perms:
            return True
        return any(code in perms for code in perm_codes)
    
    def has_all_perms(self, perm_codes: List[str]) -> bool:
        """检查用户是否拥有所有权限"""
        perms = self.all_permissions
        if "*" in perms:
            return True
        return all(code in perms for code in perm_codes)
    
    @property
    def role_names(self) -> List[str]:
        """获取用户的角色名称列表"""
        return [role.name for role in self.roles]


# ========== 用户表级权限 (User Table Permission) ==========

class UserTablePermission(Base):
    """
    用户表级权限表
    
    存储用户对特定数据库表的访问权限
    """
    __tablename__ = 'sys_user_table_permissions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey('sys_users.id', ondelete='CASCADE'), nullable=False, index=True)
    table_name = Column(String(128), nullable=False, index=True)
    
    # 唯一约束：每个用户对每个表只有一条记录
    __table_args__ = (
        UniqueConstraint('user_id', 'table_name', name='uq_user_table'),
    )
    
    def __repr__(self):
        return f"<UserTablePermission user={self.user_id} table={self.table_name}>"


