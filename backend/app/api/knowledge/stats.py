"""
知识库 API - 统计与溯源路由
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from app.core.security.auth import User
from app.core.security.rbac_deps import CheckPerm
from app.services.graph_service import GraphService
get_current_admin = CheckPerm("knowledge:manage")
from app.services.filesystem_service import FilesystemService
from app.models.auth.rbac import UserModel
from app.models.auth.organization import DepartmentModel

from .deps import get_user_context, get_graph_service, get_filesystem_service
from .schemas import DocumentOrigin
from app.models.common.context import UserContext

router = APIRouter(tags=["stats"])
logger = logging.getLogger(__name__)


@router.get("/stats")
async def get_document_stats(
    user_context: UserContext = Depends(get_user_context),
    graph: GraphService = Depends(get_graph_service)
):
    """获取文档统计信息"""
    return await graph.get_knowledge_stats(
        user_id=user_context.user_id,
        dept_id=user_context.dept_id,
        is_admin=any(code in user_context.capabilities for code in ('*', 'knowledge:manage')),
        workspace_id=user_context.workspace_id
    )


@router.get("/documents/{document_id}/origin", response_model=DocumentOrigin)
async def get_document_origin(
    document_id: str,
    user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service),
    graph: GraphService = Depends(get_graph_service)
):
    """获取文档溯源信息（含切片统计）"""
    import asyncio
    import chromadb
    from pathlib import Path
    from app.config import get_settings
    
    try:
        file_record = await filesystem.get_file(document_id)
        if not file_record:
            raise HTTPException(404, "文档不存在")
        if file_record.workspace_id != user.workspace_id:
            raise HTTPException(403, "无权访问该文档")
        
        owner_id = file_record.owner_id or file_record.user_id or "unknown"
        owner_name = "未知用户"
        department_name = None
        
        users_table = UserModel.__tablename__
        depts_table = DepartmentModel.__tablename__
        
        result = await graph.db.execute(
            text(f"""
                SELECT u.username, d.name 
                FROM {users_table} u 
                LEFT JOIN {depts_table} d ON u.department_id = d.id 
                WHERE u.id = :user_id
            """),
            {"user_id": owner_id}
        )
        row = result.fetchone()
        if row:
            owner_name = row[0] or "未知用户"
            department_name = row[1]
        
        # [新增] 查询 ChromaDB 切片统计
        chunk_count = 0
        chunks_with_file_id = 0
        try:
            settings = get_settings()
            persist_path = Path(settings.chroma.persist_dir).resolve()
            client = chromadb.PersistentClient(path=str(persist_path))
            collection = client.get_or_create_collection("tenant_docs")
            
            results = await asyncio.to_thread(
                collection.get,
                where={"file_id": {"$eq": document_id}},
                include=["metadatas"]
            )
            
            if results and results.get("ids"):
                chunk_count = len(results["ids"])
                for meta in results.get("metadatas", []):
                    if meta.get("file_id"):
                        chunks_with_file_id += 1
        except Exception as e:
            logger.warning(f"获取切片统计失败: {e}")
        
        file_id_coverage = f"{chunks_with_file_id}/{chunk_count}" if chunk_count > 0 else "0/0"
        
        return DocumentOrigin(
            document_id=document_id,
            name=file_record.name or "未知文件",
            owner_id=owner_id,
            owner_name=owner_name,
            department_name=department_name,
            visibility=file_record.visibility or "dept",
            created_at=str(file_record.created_at) if file_record.created_at else "",
            updated_at=str(file_record.updated_at) if file_record.updated_at else None,
            chunk_count=chunk_count,
            chunks_with_file_id=chunks_with_file_id,
            file_id_coverage=file_id_coverage
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取文档溯源信息失败: {e}")
        raise HTTPException(500, f"获取溯源信息失败: {str(e)}")


@router.get("/documents/{document_id}/chunks")
async def get_document_chunks(
    document_id: str,
    user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """
    [诊断 API] 获取文档的所有切片详情
    
    用于调试 RAG 切片是否正确存储 file_id
    
    Returns:
        chunk_count: 切片总数
        chunks_with_file_id: 有 file_id 的切片数
        chunks_without_file_id: 无 file_id 的切片数
        sample_chunks: 前 5 个切片的详情（用于检查 metadata）
    """
    import asyncio
    import chromadb
    from pathlib import Path
    from app.config import get_settings
    
    try:
        file_record = await filesystem.get_file(document_id)
        if not file_record:
            raise HTTPException(404, "文档不存在")
        if file_record.workspace_id != user.workspace_id:
            raise HTTPException(403, "无权访问该文档")
        
        settings = get_settings()
        persist_path = Path(settings.chroma.persist_dir).resolve()
        client = chromadb.PersistentClient(path=str(persist_path))
        collection = client.get_or_create_collection("tenant_docs")
        
        # 查询指定文件的所有切片
        results = await asyncio.to_thread(
            collection.get,
            where={"file_id": {"$eq": document_id}},
            include=["documents", "metadatas"]
        )
        
        chunks = []
        chunks_with_file_id = 0
        chunks_without_file_id = 0
        
        if results and results.get("ids"):
            for i, chunk_id in enumerate(results["ids"]):
                meta = results["metadatas"][i] if results.get("metadatas") else {}
                content = results["documents"][i] if results.get("documents") else ""
                
                file_id_in_meta = meta.get("file_id", "")
                if file_id_in_meta:
                    chunks_with_file_id += 1
                else:
                    chunks_without_file_id += 1
                
                chunks.append({
                    "chunk_id": chunk_id,
                    "file_id": file_id_in_meta,
                    "source_file": meta.get("source_file", ""),
                    "chunk_index": meta.get("chunk_index", -1),
                    "type": meta.get("type", "unknown"),
                    "content_preview": content[:100] + "..." if len(content) > 100 else content,
                    "has_file_id": bool(file_id_in_meta)
                })
        
        return {
            "document_id": document_id,
            "chunk_count": len(chunks),
            "chunks_with_file_id": chunks_with_file_id,
            "chunks_without_file_id": chunks_without_file_id,
            "file_id_coverage": f"{chunks_with_file_id}/{len(chunks)}" if chunks else "0/0",
            "sample_chunks": chunks[:10]  # 返回前 10 个用于检查
        }
        
    except Exception as e:
        logger.error(f"获取切片诊断信息失败: {e}")
        raise HTTPException(500, f"获取切片诊断失败: {str(e)}")

