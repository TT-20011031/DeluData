"""
DeluData - 配置管理模块

包含:
- 数据库连接配置
- SQL 示例管理
- 文档模板管理
"""

# 数据库配置
from app.models.config.db_config import (
    UserDBConfigModel,
    UserDBConfig,
    TableSchema,
    DBConnectionStatus,
    # 异步 CRUD
    save_user_db_config_async,
    get_user_db_config_async,
    is_user_connected_async,
    disconnect_user_async,
    deactivate_user_connection_async,
    # 同步兼容 (过渡期)
    save_user_db_config,
    get_user_db_config,
    is_user_connected,
    disconnect_user,
    activate_user_connection,
)
from app.models.config.db_whitelist import (
    WorkspaceDBWhitelistModel,
    WorkspaceDBWhitelist,
    DBWhitelistEndpoint,
    get_workspace_db_whitelist_async,
    save_workspace_db_whitelist_async,
)
from app.models.config.workspace_knowledge_governance import (
    WorkspaceKnowledgeGovernanceModel,
    WorkspaceKnowledgeGovernance,
)
from app.models.config.semantic import (
    SemanticDatasourceModel,
    SemanticTableModel,
    SemanticColumnModel,
    SemanticMetricModel,
    SemanticRowPermissionModel,
    SemanticAccessPolicyModel,
    SemanticAccessPolicyEffectModel,
    SemanticAssetTagModel,
    SemanticRelationshipModel,
    SemanticQueryRunModel,
    SemanticEvaluationRunModel,
    SemanticGovernancePolicyModel,
    SemanticGovernanceRunModel,
    SemanticColumnProfileModel,
    SemanticEvidenceFactModel,
    SemanticGovernanceCandidateModel,
    SemanticAccessBootstrapRunModel,
    SemanticAccessBootstrapTargetModel,
    SemanticAccessBootstrapMappingModel,
    SemanticDatasource,
    SemanticTable,
    SemanticColumn,
    SemanticMetric,
    SemanticRelationship,
    SemanticQueryRun,
    SemanticEvaluationRun,
)
from app.models.config.permission_evidence import (
    SemanticAccessEvidenceAssetModel,
    SemanticAccessEvidenceRelationModel,
    SemanticAccessEvidenceSetModel,
    SemanticPermissionEvidenceRunModel,
)

# SQL 示例
from app.models.config.sql_example import (
    SqlExampleModel,
    SqlExample,
    SqlExampleCreate,
    SqlExampleUpdate,
    create_sql_example_async,
    get_sql_example_async,
    list_sql_examples_async,
    update_sql_example_async,
    delete_sql_example_async,
    batch_delete_sql_examples_async,
    batch_move_to_group_async,
)

# SQL 示例分组
from app.models.config.sql_example_group import (
    SqlExampleGroupModel,
    SqlExampleGroup,
    SqlExampleGroupCreate,
    SqlExampleGroupUpdate,
    create_sql_example_group_async,
    list_sql_example_groups_async,
    get_sql_example_group_async,
    update_sql_example_group_async,
    delete_sql_example_group_async,
)

# SQL 示例向量化
from app.models.config.sql_example_embeddings import (
    add_sql_example_embedding,
    update_sql_example_embedding,
    delete_sql_example_embedding,
    batch_delete_sql_example_embeddings,
    clear_sql_example_embeddings,
    search_sql_examples_by_similarity,
    sync_all_sql_examples_to_vector,
)

# 模板
from app.models.config.template import (
    TemplateModel,
    Template,
    TemplateCreate,
    TemplateUpdate,
    VariableDefinition,
    create_template_async,
    get_template_async,
    list_templates_async,
    update_template_async,
    delete_template_async,
    match_templates_async,
)

# 模板分组
from app.models.config.template_group import (
    TemplateGroupModel,
    TemplateGroup,
    TemplateGroupCreate,
    TemplateGroupUpdate,
    create_template_group_async,
    get_template_group_async,
    list_template_groups_async,
    update_template_group_async,
    delete_template_group_async,
)

# 智能体配置
from app.models.config.agent_config import (
    AgentConfigModel,
    AgentConfig,
    AgentConfigUpdate,
    get_agent_config_async,
    save_agent_config_async,
    get_or_create_agent_config_async,
)

# 用户级智能体配置
from app.models.config.user_agent_config import (
    UserAgentConfigModel,
    UserAgentConfig,
    UserAgentConfigUpdate,
    UserAgentConfigResponse,
    SYSTEM_DEFAULTS,
    get_user_agent_config_async,
    save_user_agent_config_async,
    reset_user_agent_config_async,
)

# Skill 操作手册
from app.models.config.skill import (
    Skill,
    SkillVisibility,
)
from app.models.config.skill_schemas import (
    SkillStepSchema,
    SkillToolType,
    SkillVisibilityType,
    SkillCreateRequest,
    SkillUpdateRequest,
    SkillResponse,
    SkillSummaryResponse,
    SkillSearchResult,
    SkillGenerateRequest,
    SkillGenerateResponse,
    DryRunRequest,
    DryRunStepPreview,
    DryRunResponse,
)

__all__ = [
    # 数据库配置
    "UserDBConfigModel", "UserDBConfig", "TableSchema", "DBConnectionStatus",
    "save_user_db_config_async", "get_user_db_config_async", "is_user_connected_async",
    "disconnect_user_async", "deactivate_user_connection_async",
    "save_user_db_config", "get_user_db_config", "is_user_connected",
    "disconnect_user", "activate_user_connection",
    "WorkspaceDBWhitelistModel", "WorkspaceDBWhitelist", "DBWhitelistEndpoint",
    "get_workspace_db_whitelist_async", "save_workspace_db_whitelist_async",
    "WorkspaceKnowledgeGovernanceModel", "WorkspaceKnowledgeGovernance",
    "SemanticDatasourceModel", "SemanticTableModel", "SemanticColumnModel",
    "SemanticMetricModel", "SemanticRowPermissionModel", "SemanticAccessPolicyModel",
    "SemanticAccessPolicyEffectModel", "SemanticAssetTagModel", "SemanticRelationshipModel", "SemanticQueryRunModel",
    "SemanticEvaluationRunModel", "SemanticGovernancePolicyModel", "SemanticGovernanceRunModel",
    "SemanticColumnProfileModel", "SemanticEvidenceFactModel", "SemanticGovernanceCandidateModel",
    "SemanticAccessBootstrapRunModel", "SemanticAccessBootstrapTargetModel",
    "SemanticAccessBootstrapMappingModel",
    "SemanticDatasource", "SemanticTable", "SemanticColumn", "SemanticMetric",
    "SemanticRelationship", "SemanticQueryRun", "SemanticEvaluationRun",
    # SQL 示例
    "SqlExampleModel", "SqlExample", "SqlExampleCreate", "SqlExampleUpdate",
    "create_sql_example_async", "get_sql_example_async", "list_sql_examples_async",
    "update_sql_example_async", "delete_sql_example_async",
    "batch_delete_sql_examples_async", "batch_move_to_group_async",
    # SQL 示例分组
    "SqlExampleGroupModel", "SqlExampleGroup", "SqlExampleGroupCreate", "SqlExampleGroupUpdate",
    "create_sql_example_group_async", "list_sql_example_groups_async",
    "get_sql_example_group_async", "update_sql_example_group_async", "delete_sql_example_group_async",
    # SQL 示例向量化
    "add_sql_example_embedding", "update_sql_example_embedding", "delete_sql_example_embedding",
    "batch_delete_sql_example_embeddings", "clear_sql_example_embeddings",
    "search_sql_examples_by_similarity", "sync_all_sql_examples_to_vector",
    # 模板
    "TemplateModel", "Template", "TemplateCreate", "TemplateUpdate", "VariableDefinition",
    "create_template_async", "get_template_async", "list_templates_async",
    "update_template_async", "delete_template_async", "match_templates_async",
    # 模板分组
    "TemplateGroupModel", "TemplateGroup", "TemplateGroupCreate", "TemplateGroupUpdate",
    "create_template_group_async", "get_template_group_async", "list_template_groups_async",
    "update_template_group_async", "delete_template_group_async",
    # 智能体配置
    "AgentConfigModel", "AgentConfig", "AgentConfigUpdate",
    "get_agent_config_async", "save_agent_config_async", "get_or_create_agent_config_async",
    # 用户级智能体配置
    "UserAgentConfigModel", "UserAgentConfig", "UserAgentConfigUpdate", "UserAgentConfigResponse",
    "SYSTEM_DEFAULTS", "get_user_agent_config_async", "save_user_agent_config_async", "reset_user_agent_config_async",
    # Skill 操作手册
    "Skill", "SkillVisibility",
    "SkillStepSchema", "SkillToolType", "SkillVisibilityType",
    "SkillCreateRequest", "SkillUpdateRequest", "SkillResponse", "SkillSummaryResponse",
    "SkillSearchResult", "SkillGenerateRequest", "SkillGenerateResponse",
    "DryRunRequest", "DryRunStepPreview", "DryRunResponse",
]

