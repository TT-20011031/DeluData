"""
DeluData 智能问数系统 - 组织架构模型

支持无限层级部门树 + 物化路径
"""
from typing import List, Optional
from sqlalchemy import Column, String, Integer, ForeignKey, Boolean
from sqlalchemy.orm import relationship, backref

from app.core.db.database import Base


class DepartmentModel(Base):
    """
    部门模型 (支持无限层级树形结构)
    
    使用物化路径(Materialized Path)存储层级关系
    例如: ancestors = "/1/5/12/" 表示 根->1号部门->5号部门->12号部门
    """
    __tablename__ = 'sys_departments'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(36), nullable=False, index=True)  # 租户隔离
    parent_id = Column(Integer, ForeignKey('sys_departments.id'), nullable=True)  # 父部门
    
    name = Column(String(64), nullable=False)
    code = Column(String(32), nullable=True, index=True)  # 部门编码
    leader_id = Column(String(36), nullable=True)  # 部门负责人ID
    
    # 物化路径: 格式如 "/1/5/12/"，用于快速查询所有子部门
    ancestors = Column(String(255), default='', nullable=False)
    order_num = Column(Integer, default=0)  # 排序
    status = Column(Boolean, default=True)  # 启用状态
    
    # 自关联: 子部门
    children = relationship(
        'DepartmentModel',
        backref=backref('parent', remote_side=[id]),
        lazy='dynamic'
    )
    
    def __repr__(self):
        return f"<Department {self.name}>"
    
    def get_ancestors_list(self) -> List[int]:
        """获取所有祖先部门ID列表"""
        if not self.ancestors:
            return []
        return [int(x) for x in self.ancestors.strip('/').split('/') if x]
    
    def build_ancestors(self) -> str:
        """构建物化路径"""
        if self.parent:
            return f"{self.parent.ancestors}{self.parent.id}/"
        return "/"
    
    @property
    def level(self) -> int:
        """获取层级深度 (根=0)"""
        return len(self.get_ancestors_list())


# ========== 数据范围枚举 ==========

class DataScope:
    """数据范围常量"""
    ALL = 1           # 全部数据
    DEPT_TREE = 2     # 本部门及下属部门
    DEPT_ONLY = 3     # 仅本部门
    PERSONAL = 4      # 仅本人
    
    @classmethod
    def get_label(cls, scope: int) -> str:
        labels = {
            cls.ALL: "全部数据",
            cls.DEPT_TREE: "本部门及下属",
            cls.DEPT_ONLY: "本部门",
            cls.PERSONAL: "仅本人"
        }
        return labels.get(scope, "未知")
