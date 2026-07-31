# RAG (Retrieval-Augmented Generation) module
# 使用惰性导入避免循环依赖，外部应直接从子模块导入
# 例如: from app.core.rag.hybrid_retriever import HybridRetriever

__all__ = [
    "hybrid_retriever",
    "semantic_chunker",
    "reranker",
    "query_rewriter",
    "context_expander",
    "data_context",
    "summary_service",
    "base_retriever",
    "retriever_factory",
    "pageindex",
    # v2.5 新增组件化模块
    "document_parser",
    "document_ingestor",
    "image_linker",
]

