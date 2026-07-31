"""
DeluData 智能问数系统 - 数据模型包

模块化结构:
- auth/      认证与权限 (RBAC, 组织架构)
- config/    配置管理 (SQL示例, 模板, 数据库配置)
- knowledge/ 知识库 (文件, 文件夹, 关系图谱)
- common/    通用模型 (上下文, 执行结果, 枚举)
- system/    系统级 (LangGraph Checkpoints)

注意: 向后兼容层已移除，请使用完整路径导入:
- app.models.auth.rbac
- app.models.auth.organization
- app.models.config.db_config
- app.models.config.sql_example
- app.models.config.sql_example_group
- app.models.config.sql_example_embeddings
- app.models.config.template
- app.models.config.template_group
- app.models.knowledge.graph
- app.models.knowledge.legacy
- app.models.common.context
- app.models.common.execution
- app.models.common.enums
- app.models.system.checkpoints
"""

# 子模块导出 (仅提供简洁的访问路径)
from app.models import auth
from app.models import config
from app.models import knowledge
from app.models import common
from app.models import system
from app.models import wiki
