"""
DeluData 智能问数系统 - 用户上下文模型
定义用户权限上下文，用于全链路权限校验
"""
from typing import List, Optional
from pydantic import BaseModel, Field
class UserContext(BaseModel):
    """
    用户上下文
    贯穿整个请求链路，用于权限校验
    Attributes:
        user_id: 用户唯一标识
        workspace_id: 工作空间/租户ID (为多租户预留)
        username: 用户名
        role: 用户角色 (兼容旧逻辑)
        dept_id: 部门ID (数据权限隔离)
        data_scope: 数据范围 (1=全部, 2=本部门及以下, 3=本部门, 4=仅本人)
        roles: 角色代码列表
        allowed_tables: 用户有权访问的表列表
        allowed_datasources: 用户有权访问的数据源列表
        sandbox_path: 沙盒目录路径 (OfficeWorker 使用)
        file_context: 文件上下文 (OfficeWorker 使用)
    """
    user_id: str = Field(..., description="用户唯一标识")
    workspace_id: str = Field(default="default", description="工作空间ID")
    username: Optional[str] = Field(default=None, description="用户名")
    role: str = Field(default="user", description="用户角色")
    # 数据权限字段 (RBAC扩展)
    dept_id: Optional[int] = Field(default=None, description="部门ID")
    data_scope: int = Field(default=4, description="数据范围 (1=全部, 2=本部门及以下, 3=本部门, 4=仅本人)")
    roles: List[str] = Field(default_factory=list, description="角色代码列表")
    capabilities: List[str] = Field(default_factory=list, description="有效能力码列表")
    is_workspace_admin: bool = Field(default=False, description="是否为当前工作区管理员")
    allowed_tables: List[str] = Field(default_factory=list, description="允许访问的表")
    allowed_datasources: List[str] = Field(default_factory=list, description="允许访问的数据源")
    # OfficeWorker 相关字段
    sandbox_path: Optional[str] = Field(default=None, description="沙盒目录路径")
    file_context: Optional[dict] = Field(default=None, description="文件上下文")
    def can_access_table(self, table_name: str) -> bool:
        """检查用户是否有权访问指定表"""
        # 如果 allowed_tables 为空，表示允许访问所有表（单租户模式）
        if not self.allowed_tables:
            return True
        return table_name in self.allowed_tables
    def can_access_tables(self, table_names: List[str]) -> bool:
        """检查用户是否有权访问所有指定的表"""
        return all(self.can_access_table(t) for t in table_names)
    def get_unauthorized_tables(self, table_names: List[str]) -> List[str]:
        """获取用户无权访问的表列表"""
        if not self.allowed_tables:
            return []
        return [t for t in table_names if t not in self.allowed_tables]
class SessionContext(BaseModel):
    """
    会话上下文
    用于追踪单次对话会话的状态
    Attributes:
        session_id: 会话唯一标识
        user_context: 用户上下文
        created_at: 会话创建时间
    """
    session_id: str = Field(..., description="会话唯一标识")
    user_context: UserContext = Field(..., description="用户上下文")
    created_at: Optional[str] = Field(default=None, description="创建时间")
class TaskStep(BaseModel):
    """
    任务步骤
    Consultative Planner 的核心结构，支持可编辑参数
    Attributes:
        step_id: 步骤唯一标识
        description: 步骤描述
        worker: 执行该步骤的 Worker 类型
        status: 执行状态 (pending/running/completed/error/waiting)
        result: 执行结果
        editable: 是否可编辑（用户可修改参数）
        suggested_value: 建议的默认值
        input_type: 输入类型 (text/number/select)
        input_label: 输入框标签
        options: 下拉选项（当 input_type 为 select 时）
    """
    step_id: str = Field(..., description="步骤唯一标识")
    description: str = Field(..., description="步骤描述")
    worker: str = Field(default="sql_worker", description="执行 Worker")
    status: str = Field(default="pending", description="执行状态")
    result: Optional[str] = Field(default=None, description="执行结果")
    # [逻辑门控] 任务分类
    category: str = Field(default="extraction", description="任务分类: extraction|terminal|utility")
    # Consultative Planner 扩展字段
    editable: bool = Field(default=False, description="是否可编辑")
    suggested_value: Optional[str] = Field(default=None, description="建议值")
    input_type: Optional[str] = Field(default=None, description="输入类型")
    input_label: Optional[str] = Field(default=None, description="输入标签")
    options: List[str] = Field(default_factory=list, description="下拉选项")
    params: dict = Field(default_factory=dict, description="Step-level structured params (e.g., template_id, doc_scope)")
