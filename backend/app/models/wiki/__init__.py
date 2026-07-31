"""
DeluData - Wiki 模块（Karpathy LLM Wiki 模式）

包含 6 张核心表：
- WikiPage           实体页本体
- WikiLink           实体页之间的双向链接
- WikiPageSource     实体页 ↔ 原文档/切片溯源
- WikiRevision       修订历史
- WikiCompileTask    编译任务（与 IngestionTask 同框架，独立队列）
- WikiRouteMetric    [M3.5] 检索路由评估指标（每次 doc_worker 一行）
"""
from app.models.wiki.wiki_page import WikiPage
from app.models.wiki.wiki_link import WikiLink
from app.models.wiki.wiki_page_source import WikiPageSource
from app.models.wiki.wiki_revision import WikiRevision
from app.models.wiki.wiki_compile_task import WikiCompileTask
from app.models.wiki.wiki_route_metric import WikiRouteMetric

__all__ = [
    "WikiPage",
    "WikiLink",
    "WikiPageSource",
    "WikiRevision",
    "WikiCompileTask",
    "WikiRouteMetric",
]
