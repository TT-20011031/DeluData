"""
DeluData 智能问数系统 - 部门管理 API

部门 CRUD、树形结构管理
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.db.database import get_async_db
from app.models.auth.organization import DepartmentModel, DataScope
from app.models.auth.rbac import UserModel
from app.core.security.rbac_deps import CheckPerm
from app.core.security.auth import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/departments", tags=["部门管理"])


# ========== 请求/响应模型 ==========

def _legacy_department_api_gone():
    raise HTTPException(
        status_code=410,
        detail={"code": "legacy_api_gone", "replacement": "/api/authorization/org-units"},
    )


router.dependencies.append(Depends(_legacy_department_api_gone))


class DepartmentInfo(BaseModel):
    """部门信息"""
    id: int
    workspace_id: str
    parent_id: Optional[int]
    name: str
    code: Optional[str]
    leader_id: Optional[str]
    ancestors: str
    order_num: int
    status: bool
    level: int

class DepartmentTreeNode(BaseModel):
    """部门树节点"""
    id: int
    name: str
    code: Optional[str]
    parent_id: Optional[int]
    leader_id: Optional[str]
    children: List['DepartmentTreeNode'] = []

class CreateDepartmentRequest(BaseModel):
    """创建部门请求"""
    name: str
    code: Optional[str] = None
    parent_id: Optional[int] = None
    leader_id: Optional[str] = None
    order_num: int = 0

class UpdateDepartmentRequest(BaseModel):
    """更新部门请求"""
    name: Optional[str] = None
    code: Optional[str] = None
    leader_id: Optional[str] = None
    order_num: Optional[int] = None
    status: Optional[bool] = None


class DepartmentMemberInfo(BaseModel):
    """
    部门成员信息 (脱敏响应)
    
    仅返回必要的用户信息，不包含密码等敏感字段
    """
    id: str
    username: str
    email: Optional[str]
    primary_role: Optional[str]  # 主角色名称
    disabled: bool


# ========== API 端点 ==========

@router.get("", response_model=List[DepartmentInfo])
async def list_departments(
    current_user: User = Depends(CheckPerm("sys:user:view")),
    db: AsyncSession = Depends(get_async_db)
):
    """获取部门列表"""
    workspace_id = current_user.workspace_id
    result = await db.execute(
        select(DepartmentModel)
        .where(DepartmentModel.workspace_id == workspace_id)
        .order_by(DepartmentModel.order_num)
    )
    departments = result.scalars().all()
    
    return [
        DepartmentInfo(
            id=d.id,
            workspace_id=d.workspace_id,
            parent_id=d.parent_id,
            name=d.name,
            code=d.code,
            leader_id=d.leader_id,
            ancestors=d.ancestors,
            order_num=d.order_num,
            status=d.status,
            level=d.level
        )
        for d in departments
    ]


@router.get("/tree")
async def get_department_tree(
    current_user: User = Depends(CheckPerm("sys:user:view")),
    db: AsyncSession = Depends(get_async_db)
):
    """获取部门列表 (扁平结构)"""
    workspace_id = current_user.workspace_id
    result = await db.execute(
        select(DepartmentModel)
        .where(DepartmentModel.workspace_id == workspace_id, DepartmentModel.status == True)
        .order_by(DepartmentModel.order_num)
    )
    departments = result.scalars().all()
    
    # 直接返回扁平列表，结构与 /tree 预期兼容 (虽名为 tree 但前端已按 list 处理)
    return [
        {
            "id": d.id,
            "name": d.name,
            "code": d.code,
            "parent_id": None, # 强制隐藏层级
            "leader_id": d.leader_id,
            "children": [] # 始终为空
        }
        for d in departments
    ]



@router.post("", response_model=DepartmentInfo)
async def create_department(
    request: CreateDepartmentRequest,
    current_user: User = Depends(CheckPerm("sys:user:edit")),
    db: AsyncSession = Depends(get_async_db)
):
    """创建部门 (强制一级部门)"""
    workspace_id = current_user.workspace_id
    # 检查重名
    exists = await db.execute(
        select(DepartmentModel).where(
            DepartmentModel.name == request.name,
            DepartmentModel.workspace_id == workspace_id
        )
    )
    if exists.scalars().first():
        raise HTTPException(status_code=400, detail="部门名称已存在")

    # 强制扁平化：无父部门，无路径
    dept = DepartmentModel(
        workspace_id=workspace_id,
        parent_id=None,
        name=request.name,
        code=request.code,
        leader_id=request.leader_id,
        ancestors='',
        order_num=request.order_num,
        status=True
    )
    
    db.add(dept)
    await db.commit()
    await db.refresh(dept)
    
    logger.info(f"创建一级部门: {dept.name} (id={dept.id})")
    
    return DepartmentInfo(
        id=dept.id,
        workspace_id=dept.workspace_id,
        parent_id=None,
        name=dept.name,
        code=dept.code,
        leader_id=dept.leader_id,
        ancestors='',
        order_num=dept.order_num,
        status=dept.status,
        level=1
    )


@router.put("/{dept_id}", response_model=DepartmentInfo)
async def update_department(
    dept_id: int,
    request: UpdateDepartmentRequest,
    current_user: User = Depends(CheckPerm("sys:user:edit")),
    db: AsyncSession = Depends(get_async_db)
):
    """更新部门"""
    result = await db.execute(
        select(DepartmentModel).where(
            DepartmentModel.id == dept_id,
            DepartmentModel.workspace_id == current_user.workspace_id
        )
    )
    dept = result.scalar_one_or_none()
    
    if not dept:
        raise HTTPException(status_code=404, detail="部门不存在")
    
    if request.name is not None:
        dept.name = request.name
    if request.code is not None:
        dept.code = request.code
    if request.leader_id is not None:
        dept.leader_id = request.leader_id
    if request.order_num is not None:
        dept.order_num = request.order_num
    if request.status is not None:
        dept.status = request.status
    
    await db.commit()
    await db.refresh(dept)
    
    logger.info(f"更新部门: {dept.name}")
    
    return DepartmentInfo(
        id=dept.id,
        workspace_id=dept.workspace_id,
        parent_id=dept.parent_id,
        name=dept.name,
        code=dept.code,
        leader_id=dept.leader_id,
        ancestors=dept.ancestors,
        order_num=dept.order_num,
        status=dept.status,
        level=dept.level
    )


@router.delete("/{dept_id}")
async def delete_department(
    dept_id: int,
    current_user: User = Depends(CheckPerm("sys:user:edit")),
    db: AsyncSession = Depends(get_async_db)
):
    """删除部门 (需先删除子部门)"""
    result = await db.execute(
        select(DepartmentModel).where(
            DepartmentModel.id == dept_id,
            DepartmentModel.workspace_id == current_user.workspace_id
        )
    )
    dept = result.scalar_one_or_none()
    
    if not dept:
        raise HTTPException(status_code=404, detail="部门不存在")
    

    
    await db.delete(dept)
    await db.commit()
    
    logger.info(f"删除部门: {dept.name}")
    
    return {"success": True, "message": "部门已删除"}


@router.get("/{dept_id}/members", response_model=List[DepartmentMemberInfo])
async def get_department_members(
    dept_id: int,
    current_user: User = Depends(CheckPerm("sys:user:view")),
    db: AsyncSession = Depends(get_async_db)
):
    """
    获取部门成员列表
    
    Args:
        dept_id: 部门ID
        
    Returns:
        部门成员列表 (脱敏响应，不包含密码等敏感字段)
    """
    # 验证部门存在
    dept_result = await db.execute(
        select(DepartmentModel).where(
            DepartmentModel.id == dept_id,
            DepartmentModel.workspace_id == current_user.workspace_id
        )
    )
    dept = dept_result.scalar_one_or_none()
    if not dept:
        raise HTTPException(status_code=404, detail="部门不存在")
    
    # 构建查询的部门 ID 列表
    dept_ids = [dept_id]

    
    # 查询成员 (预加载角色)
    result = await db.execute(
        select(UserModel)
        .where(
            UserModel.department_id.in_(dept_ids),
            UserModel.workspace_id == current_user.workspace_id
        )
        .options(selectinload(UserModel.roles))
        .order_by(UserModel.username)
    )
    users = result.scalars().all()
    
    # 脱敏响应
    return [
        DepartmentMemberInfo(
            id=u.id,
            username=u.username,
            email=u.email,
            primary_role=u.roles[0].name if u.roles else None,
            disabled=u.disabled
        )
        for u in users
    ]


# ========== 工具函数 ==========

async def get_sub_department_ids(db: AsyncSession, dept_id: int) -> List[int]:
    """
    获取部门ID (已扁平化，仅返回自身)
    """
    return [dept_id]
