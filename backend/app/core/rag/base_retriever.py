"""
检索器抽象基类
"""
from abc import ABC, abstractmethod
from typing import List, Optional

from app.models.common.context import UserContext
from app.models.common.execution import DocumentChunk


class BaseRetriever(ABC):
    """统一检索器接口"""

    @abstractmethod
    async def search(
        self,
        queries: List[str],
        user_context: UserContext,
        top_n: Optional[int] = None,
        **kwargs
    ) -> List[DocumentChunk]:
        """执行检索"""
        raise NotImplementedError

