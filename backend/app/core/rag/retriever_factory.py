"""
检索器工厂
"""
from typing import Optional

import chromadb

from app.core.rag.base_retriever import BaseRetriever
from app.core.rag.hybrid_retriever import get_hybrid_retriever
from app.core.rag.pageindex.retriever import PageIndexRetriever


def get_retriever(mode: str, chroma_client: Optional[chromadb.ClientAPI] = None) -> BaseRetriever:
    """
    mode:
    - fast: 普通检索
    - enhanced: 深度检索
    """
    if mode == "enhanced":
        return PageIndexRetriever(hybrid_retriever=get_hybrid_retriever(chroma_client))
    return get_hybrid_retriever(chroma_client)

