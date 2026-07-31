"""
PageIndex 树索引构建器
"""
import logging
from pathlib import Path
from typing import Dict, List, Optional

import fitz
from sqlalchemy import select

from app.config import get_settings
from app.core.db.database import get_async_db_context
from app.core.rag.pageindex.core import page_index
from app.core.rag.pageindex.tree_store import get_tree_store
from app.models.knowledge.graph import File

logger = logging.getLogger(__name__)


class TreeBuilder:
    def __init__(self):
        self._settings = get_settings().pageindex
        self._tree_store = get_tree_store()

    async def build_and_store(
        self,
        *,
        file_id: str,
        workspace_id: str,
        file_path: str,
        visibility: str,
        owner_id: Optional[str],
        dept_id: Optional[int],
    ) -> int:
        if not self._settings.enabled:
            return 0

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        if path.suffix.lower() != ".pdf":
            # 非 PDF 静默跳过，不视为错误
            return 0

        page_count = self._get_pdf_page_count(path)
        if page_count < self._settings.min_pages:
            logger.info(
                "[PageIndex] 跳过构建: file_id=%s, pages=%s, min_pages=%s",
                file_id,
                page_count,
                self._settings.min_pages,
            )
            return 0

        raw_result = await page_index(
            str(path),
            model=self._settings.llm_model,
            toc_check_page_num=self._settings.toc_check_page_num,
            max_page_num_each_node=self._settings.max_page_num_each_node,
            max_token_num_each_node=self._settings.max_token_num_each_node,
            if_add_node_id=True,
            if_add_node_summary=True,
            if_add_doc_description=False,
            if_add_node_text=True,
        )

        structure = raw_result.get("structure", []) if isinstance(raw_result, dict) else []
        nodes = self._flatten_structure(structure, file_id=file_id)
        if not nodes:
            return 0

        return await self._tree_store.replace_nodes(
            file_id=file_id,
            workspace_id=workspace_id,
            nodes=nodes,
            visibility=visibility,
            owner_id=owner_id,
            dept_id=dept_id,
        )

    async def mark_file_index_status(self, file_id: str, status: Optional[str], error: Optional[str] = None) -> None:
        async with get_async_db_context() as session:
            stmt = select(File).where(File.id == file_id)
            result = await session.execute(stmt)
            model = result.scalar_one_or_none()
            if model is None:
                return

            model.pageindex_status = status
            model.pageindex_error = error
            await session.commit()

    def _flatten_structure(self, structure: List[Dict], file_id: str) -> List[Dict]:
        result: List[Dict] = []

        def walk(nodes: List[Dict], parent_node_id: Optional[str], level: int):
            for index, node in enumerate(nodes or []):
                node_id = str(node.get("node_id") or f"{level}_{index}")
                content = node.get("text") or ""

                result.append(
                    {
                        "file_id": file_id,
                        "node_id": node_id,
                        "parent_node_id": parent_node_id,
                        "title": str(node.get("title") or "未命名节点"),
                        "summary": node.get("summary"),
                        "content": content,
                        "start_page": node.get("start_index"),
                        "end_page": node.get("end_index"),
                        "node_level": level,
                        "sort_order": index,
                        "token_count": max(1, int(len(content) / 4)) if content else 0,
                        "meta_info": {
                            "prefix_summary": node.get("prefix_summary"),
                        },
                    }
                )
                walk(node.get("nodes", []), node_id, level + 1)

        walk(structure, None, 0)
        return result

    @staticmethod
    def _get_pdf_page_count(path: Path) -> int:
        doc = fitz.open(str(path))
        try:
            return int(doc.page_count or 0)
        finally:
            doc.close()


_builder: Optional[TreeBuilder] = None


def get_tree_builder() -> TreeBuilder:
    global _builder
    if _builder is None:
        _builder = TreeBuilder()
    return _builder
