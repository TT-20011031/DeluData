"""Wiki API 的 Pydantic Schema 定义。"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class WikiPageSummary(BaseModel):
    id: str
    slug: str
    title: str
    summary: Optional[str] = None
    domain: str
    status: str
    version: int
    char_count: int
    last_compiled_at: Optional[str] = None
    updated_at: Optional[str] = None
    governance: Optional[dict[str, Any]] = None
    source_scope: Optional[dict[str, Any]] = None


class WikiPageListResponse(BaseModel):
    total: int
    items: list[WikiPageSummary]


class WikiGraphNode(BaseModel):
    id: str
    node_type: str = "entity"
    slug: str
    title: str
    summary: Optional[str] = None
    domain: str
    status: str
    degree: int = 0
    source_scope: Optional[dict[str, Any]] = None
    updated_at: Optional[str] = None
    file_id: Optional[str] = None
    file_type: Optional[str] = None
    file_name: Optional[str] = None
    source_count: int = 0


class WikiGraphEdge(BaseModel):
    id: str
    source: str
    target: str
    link_type: str
    status: str
    confidence: float = 1.0
    note: Optional[str] = None
    evidence_count: int = 0


class WikiGraphStats(BaseModel):
    page_count: int = 0
    link_count: int = 0
    isolated_count: int = 0
    domain_counts: dict[str, int] = Field(default_factory=dict)
    link_type_counts: dict[str, int] = Field(default_factory=dict)
    entity_count: int = 0
    file_count: int = 0
    wiki_link_count: int = 0
    source_edge_count: int = 0
    total_edge_count: int = 0


class WikiGraphResponse(BaseModel):
    nodes: list[WikiGraphNode] = Field(default_factory=list)
    edges: list[WikiGraphEdge] = Field(default_factory=list)
    stats: WikiGraphStats = Field(default_factory=WikiGraphStats)


class WikiLinkBrief(BaseModel):
    id: str
    link_type: str
    status: str
    note: Optional[str] = None
    target_slug: Optional[str] = None
    target_title: Optional[str] = None
    source_slug: Optional[str] = None
    source_title: Optional[str] = None


class WikiEvidenceChunkBrief(BaseModel):
    chunk_id: str
    file_id: str
    file_name: Optional[str] = None
    page_number: Optional[int] = None
    preview: str = ""


class WikiSourceBrief(BaseModel):
    id: str
    file_id: str
    file_name: Optional[str] = None
    chunk_ids: list[str] = Field(default_factory=list)
    evidence_chunks: list[WikiEvidenceChunkBrief] = Field(default_factory=list)
    excerpt: Optional[str] = None
    visibility: Optional[str] = None
    department_id: Optional[int] = None
    owner_id: Optional[str] = None
    document_type: Optional[str] = None
    business_domain: Optional[str] = None
    confidentiality_level: Optional[str] = None
    effective_from: Optional[str] = None
    effective_until: Optional[str] = None
    external_ref: Optional[str] = None


class WikiRevisionBrief(BaseModel):
    version: int
    committed_by: str
    commit_message: Optional[str] = None
    committed_at: Optional[str] = None


class WikiPageDetail(BaseModel):
    id: str
    slug: str
    title: str
    aliases: list[str] = Field(default_factory=list)
    domain: str
    status: str
    summary: Optional[str] = None
    markdown_body: str
    version: int
    char_count: int
    token_count: int
    last_compiled_at: Optional[str] = None
    last_compiled_by: Optional[str] = None
    compile_meta: Optional[dict[str, Any]] = None
    outgoing_links: list[WikiLinkBrief] = Field(default_factory=list)
    incoming_links: list[WikiLinkBrief] = Field(default_factory=list)
    sources: list[WikiSourceBrief] = Field(default_factory=list)
    revisions: list[WikiRevisionBrief] = Field(default_factory=list)
    governance: Optional[dict[str, Any]] = None
    source_scope: Optional[dict[str, Any]] = None


class WikiPageUpdateRequest(BaseModel):
    markdown_body: Optional[str] = None
    summary: Optional[str] = None
    title: Optional[str] = None
    domain: Optional[str] = None
    status: Optional[str] = None
    commit_message: Optional[str] = None


class WikiCandidateGovernanceItem(WikiPageSummary):
    governance: dict[str, Any] = Field(default_factory=dict)


class WikiCandidateGovernanceResponse(BaseModel):
    total: int
    items: list[WikiCandidateGovernanceItem] = Field(default_factory=list)


class WikiGovernanceActionRequest(BaseModel):
    action: Literal[
        "publish",
        "verify",
        "draft",
        "archive",
        "deprecate",
        "boost",
        "unboost",
        "mark_duplicate",
        "mark_conflict",
        "merge",
    ]
    target_slug: Optional[str] = None
    note: Optional[str] = None
    commit_message: Optional[str] = None


class WikiCompileRequest(BaseModel):
    """编译请求体。
    
    file_ids 与 scope 二选一：
    - 提供 file_ids 时，仅编译指定文件
    - scope='workspace' 时，编译整个工作区已索引文件（M1 验收常用）
    """

    file_ids: Optional[list[str]] = None
    scope: Optional[str] = Field(default=None, description="'workspace' or None")


class WikiLintIssueOut(BaseModel):
    issue_id: str
    issue_type: str
    severity: str
    page_id: Optional[str] = None
    page_slug: Optional[str] = None
    page_title: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)


class WikiLintReportOut(BaseModel):
    workspace_id: str
    generated_at: str
    page_count: int
    link_count: int
    open_conflicts: int
    issues: list[WikiLintIssueOut] = Field(default_factory=list)


# ============ [M3.5] 路由评估 + 范围选择支持 ============


class WikiPathBucket(BaseModel):
    count: int = 0
    avg_latency_ms: int = 0
    wiki_gap_count: int = 0


class WikiRouteSummary(BaseModel):
    """近 N 天检索路由汇总（GET /api/wiki/metrics/route）。"""

    window_days: int
    since: str
    total: int
    by_path: dict[str, WikiPathBucket]
    wiki_hit_rate: float = 0.0
    wiki_gap_rate: float = 0.0
    avg_latency_ms: int = 0
    wiki_fallback_to_rag_count: int = 0
    wrong_tool_repair_count: int = 0
    wiki_hit_but_not_used_count: int = 0


class WikiDomainBucket(BaseModel):
    """Wiki 域聚合（前端 KnowledgeScopePicker 一级 Tab 数据源）。"""

    domain: str
    page_count: int
    sample_titles: list[str] = Field(default_factory=list)


class WikiDomainListResponse(BaseModel):
    items: list[WikiDomainBucket]


# ============ [M4.2] 配额运行时校验 ============


class WikiQuotaPagesUsage(BaseModel):
    current: int
    limit: int
    usage_pct: float = 0.0


class WikiQuotaLinksUsage(BaseModel):
    max_per_page: int
    max_outgoing_observed: int = 0


class WikiQuotaStatus(BaseModel):
    """工作区 Wiki 配额使用情况（GET /api/wiki/quota）。"""

    workspace_id: str
    max_pages_per_workspace: int
    max_links_per_page: int
    pages: WikiQuotaPagesUsage
    links: WikiQuotaLinksUsage
    near_limit: bool = False


# ============ 召回诊断台 ============


class RetrievalDiagnosticsRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    knowledge_path: Optional[Literal["rag", "wiki", "both"]] = Field(
        default=None,
        description="rag | wiki | both；为空时由诊断服务按规则判断",
    )
    doc_scope: Optional[dict[str, Any]] = Field(
        default=None,
        description="可选知识范围，沿用聊天侧 doc_scope 结构",
    )
    top_k: int = Field(default=8, ge=1, le=20)
    deep_search: bool = False


class RetrievalDiagnosticHit(BaseModel):
    source: str
    chunk_id: str
    title: Optional[str] = None
    file_id: Optional[str] = None
    file_name: Optional[str] = None
    page_number: Optional[int] = None
    score: float = 0.0
    rerank_score: Optional[float] = None
    status: Optional[str] = None
    domain: Optional[str] = None
    kept: bool = True
    reason: Optional[str] = None
    preview: str = ""
    content: str = ""


class RetrievalDiagnosticsResponse(BaseModel):
    query: str
    knowledge_path: str
    knowledge_path_source: str
    knowledge_path_reason: str
    permissions: dict[str, Any] = Field(default_factory=dict)
    metadata_filter_summary: dict[str, Any] = Field(default_factory=dict)
    route_trace: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    hits: list[RetrievalDiagnosticHit] = Field(default_factory=list)
    final_context: list[RetrievalDiagnosticHit] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class ConversationDiagnosticContextItem(BaseModel):
    source: Optional[str] = None
    title: Optional[str] = None
    slug: Optional[str] = None
    file_name: Optional[str] = None
    page_number: Optional[int] = None
    preview: str = ""


class ConversationDiagnosticItem(BaseModel):
    id: str
    created_at: str = ""
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    knowledge_path: str = "rag"
    knowledge_path_source: Optional[str] = None
    knowledge_path_reason: Optional[str] = None
    user_query: str = ""
    chunks_used: int = 0
    wiki_chunks_count: int = 0
    wiki_chunks_used: int = 0
    rag_chunks_used: int = 0
    wiki_fallback_to_rag: bool = False
    answer_context_source: str = "empty"
    issues: list[str] = Field(default_factory=list)
    final_context: list[ConversationDiagnosticContextItem] = Field(default_factory=list)
    tool_repair_trace: list[dict[str, Any]] = Field(default_factory=list)
    wrong_tool_repair: bool = False
    route_action: Optional[str] = None
    repair_reason: Optional[str] = None


class ConversationDiagnosticsResponse(BaseModel):
    items: list[ConversationDiagnosticItem] = Field(default_factory=list)


# ============ 治理：孤儿页清理 ============


class WikiPurgedPageItem(BaseModel):
    """单个被清理的实体页摘要。"""

    id: str
    slug: str
    title: str
    previous_status: str
    reason: str


class WikiPurgeOrphansResponse(BaseModel):
    """POST /api/wiki/purge-orphans 返回。"""

    workspace_id: str
    scanned_count: int
    purged_count: int
    purged: list[WikiPurgedPageItem] = Field(default_factory=list)


# ============ 治理：批量归档 ============


class WikiBatchArchiveRequest(BaseModel):
    """批量归档请求体。

    slugs 和 domain 至少提供一个：
    - slugs: 按 slug 列表精确归档（优先，domain 被忽略）
    - domain: 归档整个域下所有非 archived 页面
    """

    slugs: Optional[list[str]] = Field(
        default=None,
        min_length=1,
        description="按 slug 列表归档；与 domain 二选一",
    )
    domain: Optional[str] = Field(
        default=None,
        min_length=1,
        description="按 domain 归档整个域；仅在 slugs 为空时生效",
    )


class WikiBatchArchiveResponse(BaseModel):
    """POST /api/wiki/pages/batch-archive 返回。"""

    workspace_id: str
    requested_count: int = Field(default=0, description="请求归档的页面数（slugs 模式）或域下页面数（domain 模式）")
    archived_count: int = Field(default=0)
    skipped_count: int = Field(default=0, description="已跳过（已是 archived 或不存在）")
    archived: list[WikiPurgedPageItem] = Field(default_factory=list)
    skipped_slugs: list[str] = Field(default_factory=list)


# ============ [体验] 异步编译：入队 + 任务状态查询 ============


class WikiCompileTaskAccepted(BaseModel):
    """POST /api/wiki/compile?async_=true 入队成功后返回。

    前端拿到 task_id 后用 GET /api/wiki/tasks/{task_id} 轮询进度。
    """

    task_id: str
    status: str = Field(
        description="pending | running | cancel_requested | succeeded | failed | cancelled"
    )
    trigger_type: str
    workspace_id: str
    deduplicated: bool = Field(
        default=False,
        description="True 表示同 workspace + trigger 已有 active 任务，本次复用",
    )


class WikiCompileTaskStatus(BaseModel):
    """GET /api/wiki/tasks/{task_id} 任务状态轮询返回。"""

    task_id: str
    workspace_id: str
    trigger_type: str
    status: str = Field(
        description="pending | running | cancel_requested | succeeded | failed | cancelled"
    )
    stage: Optional[str] = Field(
        default=None,
        description="queued / extracting / compiling / completed / cancelled / failed",
    )
    progress: int = Field(default=0, ge=0, le=100)
    attempt: int = 0
    max_attempts: int = 1
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_message: Optional[str] = None
    result: Optional[dict[str, Any]] = Field(
        default=None,
        description="终态摘要（succeeded / cancelled 时含 created/files/elapsed_seconds 等）",
    )


class WikiCompileTaskCancelResponse(BaseModel):
    """POST /api/wiki/tasks/{task_id}/cancel 取消请求响应。"""

    task_id: str
    previous_status: Optional[str] = Field(
        default=None, description="取消前的 status（not_found 时为 null）"
    )
    outcome: str = Field(
        description=(
            "cancelled_immediately | cancel_requested | already_cancelling | "
            "already_terminal | not_found"
        )
    )
