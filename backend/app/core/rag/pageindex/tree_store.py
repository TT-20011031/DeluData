"""
PageIndex 树索引存储
"""
import json
from typing import Iterable, List, Optional

from sqlalchemy import delete, select

from app.core.db.database import get_async_db_context
from app.models.knowledge.tree_node import TreeNode


class TreeStore:
    async def replace_nodes(
        self,
        *,
        file_id: str,
        workspace_id: str,
        nodes: Iterable[dict],
        visibility: str,
        owner_id: Optional[str],
        dept_id: Optional[int],
    ) -> int:
        async with get_async_db_context() as session:
            await session.execute(
                delete(TreeNode).where(
                    TreeNode.file_id == file_id,
                    TreeNode.workspace_id == workspace_id,
                )
            )

            insert_count = 0
            for index, node in enumerate(nodes):
                meta_info = node.get("meta_info")
                if isinstance(meta_info, (dict, list)):
                    meta_info = json.dumps(meta_info, ensure_ascii=False)

                model = TreeNode(
                    file_id=file_id,
                    workspace_id=workspace_id,
                    node_id=str(node.get("node_id") or f"n_{index}"),
                    parent_node_id=node.get("parent_node_id"),
                    title=str(node.get("title") or "未命名节点"),
                    summary=node.get("summary"),
                    content=node.get("content"),
                    start_page=node.get("start_page"),
                    end_page=node.get("end_page"),
                    node_level=int(node.get("node_level") or 0),
                    sort_order=int(node.get("sort_order") or index),
                    token_count=int(node.get("token_count") or 0),
                    visibility=visibility,
                    owner_id=owner_id,
                    dept_id=dept_id,
                    meta_info=meta_info,
                )
                session.add(model)
                insert_count += 1

            await session.commit()
            return insert_count

    async def get_nodes_for_files(
        self,
        *,
        file_ids: List[str],
        workspace_id: str,
        allowed_visibilities: Optional[List[str]] = None,
        owner_id: Optional[str] = None,
        allowed_dept_ids: Optional[List[int]] = None,
    ) -> List[TreeNode]:
        if not file_ids:
            return []

        async with get_async_db_context() as session:
            stmt = select(TreeNode).where(
                TreeNode.file_id.in_(file_ids),
                TreeNode.workspace_id == workspace_id,
            )

            if allowed_visibilities:
                stmt = stmt.where(TreeNode.visibility.in_(allowed_visibilities))

            if owner_id is not None and allowed_dept_ids is not None:
                stmt = stmt.where(
                    (TreeNode.visibility == "public")
                    | (TreeNode.owner_id == owner_id)
                    | ((TreeNode.visibility == "dept") & TreeNode.dept_id.in_(allowed_dept_ids))
                )

            stmt = stmt.order_by(TreeNode.file_id.asc(), TreeNode.node_level.asc(), TreeNode.sort_order.asc())
            result = await session.execute(stmt)
            return list(result.scalars().all())


_store: Optional[TreeStore] = None


def get_tree_store() -> TreeStore:
    global _store
    if _store is None:
        _store = TreeStore()
    return _store
