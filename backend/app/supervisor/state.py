"""
Supervisor State 定义

包含 SupervisorState TypedDict 和相关 Reducer 函数
"""
import operator
from typing import TypedDict, Annotated, Literal, Optional

from langgraph.graph.message import add_messages


# ========== 自定义 Reducer 函数 ==========

def merge_dicts(old: dict, new: dict) -> dict:
    """字典合并自定义 Reducer"""
    if not old: old = {}
    if not new: new = {}
    return {**old, **new}


def replace_dict(old: dict, new: dict) -> dict:
    """字典替换 Reducer（用于 latest_quality_signal 这类单值状态）"""
    if new is None:
        return old or {}
    return new


def merge_assets(old: list, new) -> list:
    """
    多模态资产合并 Reducer
    
    支持两种模式：
    1. 默认模式：new 为 list，追加到 old
    2. 替换模式：new 为 dict {"_mode": "replace", "data": [...]}，直接替换
    """
    if not old: old = []
    if not new: return old
    
    # 替换模式（用于 GC 清理）
    if isinstance(new, dict) and new.get("_mode") == "replace":
        return new.get("data", [])
    
    # 默认追加模式
    if isinstance(new, list):
        return old + new
    
    return old


# ========== 状态定义 ==========

# 注意：TaskStep 的完整定义在 app.models.context 中
# 此处 SupervisorState 使用 list[dict] 以保持 TypedDict 的简洁性
# planner_node 中使用 TaskStep (Pydantic Model) 进行验证和序列化

class SupervisorState(TypedDict):
    """Supervisor 状态"""
    # 输入
    user_query: str
    user_context: dict  # UserContext 序列化
    session_id: str
    plan_id: str  # 计划 ID，用于 SSE 消息关联
    
    # 对话历史 (关键变更：使用 append-only 模式)
    messages: Annotated[list, add_messages]
    summary: str  # 长期记忆摘要
    
    # 规划与执行
    task_plan: list[dict]  # TaskStep 字典列表，由 planner_node 验证
    plan_summary: str
    plan_status: Literal["draft", "confirmed", "executing", "completed", "error", "suspended"]
    
    # 变量引用仓库 (搭便车存储)
    memory_dfs: Annotated[dict, merge_dicts]  # key -> DataFrame
    current_focus_result: Annotated[dict, replace_dict]  # 当前正在讨论的数据结果快照
    
    # 执行结果 (用于Synthesizer)
    execution_results: list[dict]
    
    # ========== Worker 自主闭环状态 ==========
    retry_count: int  # 当前计划内修复动作次数（retry_self/switch_worker）
    round_index: int  # 会话轮次计数（用于 SSE 关联）
    latest_quality_signal: Annotated[dict, replace_dict]  # 最近一次执行质量信号
    route_action: Literal["continue", "retry_self", "switch_worker", "ask_clarify", "finish"]
    
    # ========== 自动确认机制 (Router 层处理) ==========
    need_confirm: bool  # Planner 判定是否需要用户确认，Router 层据此决定是否自动调用 confirm_and_execute
    confirmed_sql_query: bool  # 用户确认的计划包含 SQL 查询；SQL 失败/空结果时禁止改用知识库兜底

    # ========== 意图分类快速路径 ==========
    skip_planner: bool  # IntentClassifier 判定跳过 Planner（chitchat/direct_answer）
    intent_type: str  # chitchat | direct_answer | tool_use | direct_execution

    # ========== Wiki-First 知识路由 (M3.1) ==========
    knowledge_path: str  # wiki | rag | both（KnowledgeRouter 判定结果，开关关闭时恒为 rag）
    knowledge_path_reason: str  # 路由判定原因（用于调试和埋点）
    knowledge_path_source: str  # disabled | rule | llm | fallback（决策来源）
    wiki_gap_signal: Optional[dict]  # Synthesizer 检测到 Wiki 缺失时回写的信号 (M3.4 用)

    # ========== Inline HITL (Interrupt/Resume) ==========
    interrupt_signal: Optional[dict]  # 挂起信号
    
    # ========== 迭代式规划支持 ==========
    steps_executed_count: int  # 全局步骤计数器 (死循环防护)
    tool_confidence: Optional[str]  # Planner 工具选择自信度: "high" | "medium" | "low"
    
    # ========== Ping-Pong 防护 ==========
    tried_workers: list  # 已尝试过的工具列表（防止 SQL→RAG→SQL 横跳）
    # 格式: ["sql_worker", "doc_worker"]
    # Router 不应重复切换已尝试过的工具
    
    # ========== [双通道反馈] 思考节点持久化 ==========
    thought_nodes: Annotated[list[dict], operator.add]  # Router/Worker 思考历史
    # 格式: [{"id": "uuid", "thought": "...", "verdict": "fail", "round_index": 0, "timestamp": "..."}]
    # 用于页面刷新后恢复 Timeline 中的思考气泡
    reasoning_traces: Annotated[list[dict], operator.add]  # Synthesizer 安全思考摘要（按 message_id 持久化）
    # 格式: [{"message_id":"plan_x","content":"...","duration_ms":1234,"round_index":1,"safe":True}]
    
    
    # ========== Inline HITL 防重复 ==========
    interrupt_emitted: bool  # 标记 INTERRUPT 是否已发送（防止 LangGraph 恢复时重复发送）
    
    # ========== 步进式反思 (Step-wise Reflection) ==========
    last_executed_step: Optional[dict]  # 最后执行的步骤信息
    # 格式: {"step_id": "1", "worker": "sql_worker", "error": False}
    # 用于 route_after_executor 判断下一跳是否进入 RouterAgent
    
    # ========== 直连执行模式 (Direct Execution) ==========
    execution_mode: str  # auto | rag_only | sql_only | chart_only | office_only
    deep_search: bool  # DocWorker 是否启用深度检索
    reply_model_key: str  # 正常回答模型档位（plus|flash）
    final_reply_model: str  # 正常回答模型名（仅用于 Synthesizer）
    is_direct_execution: bool  # 标记是否为直连执行，供 Synthesizer 感知
    has_db_connection: bool  # 工作区 readiness 快照: 是否可用数据库
    has_knowledge_base: bool  # 工作区 readiness 快照: 是否可用知识库
    readiness_reasons: list[str]  # readiness 诊断原因

    # ========== 延迟 DataFrame 保存 (重试机制优化) ==========
    pending_artifacts: Annotated[dict, merge_dicts]  # 暂存执行过程中的 DataFrame
    # 在 Synthesizer 阶段统一合并到 memory_dfs
    # 格式: {df_key: DataFrame}
    
    # ========== 重试机制路由配置 (Executor 预存) ==========
    _max_retries: int  # 从数据库读取的 max_retries 配置，供边函数使用
    _has_terminal_tasks: bool  # 当前计划是否包含终结类任务
    
    # ========== Synthesizer 自定义 Prompt (Context 透传) ==========
    active_template_prompt: str  # 已解析的模板 Prompt 内容（Graph 入口预加载）
    synthesizer_custom_prompt: str  # 用户微调 Prompt
    
    # ========== 多模态会话资产 (上下文持久化) ==========
    active_assets: Annotated[list[dict], merge_assets]  # 使用自定义 Reducer 支持 GC 清理
    # 格式: [{
    #   "id": "img_001",
    #   "type": "image",  
    #   "url": "...",
    #   "round_added": 0,        # 添加时的轮次
    #   "last_mentioned": 1       # 最后被提及的轮次（用于 GC）
    # }]

    # ========== Skill 操作手册模式 (Phase 2) ==========
    skill_mode: bool  # 是否为 Skill 模式（匹配到手册后启用）
    selected_skill_id: Optional[str]  # 选中的 Skill ID
    selected_skill_name: Optional[str]  # Skill 名称（用于可解释性）
    skill_context: str  # 格式化的 Skill 上下文（注入 Planner Prompt）
    skill_steps: list[dict]  # Skill 的结构化步骤（用于参数稳定落地）
    # 当 skill_mode=True 时：
    # - Planner 参考 skill_context 规划步骤
    # - 任务列表 UI 可显示"参考手册: {skill_name}"
    
    # 输出
    final_answer: str
    error: Optional[str]
