"""
向量库同步清理脚本 (直接操作 ChromaDB 版)

目标：
1. 读取 PostgreSQL 数据库中的所有有效文件 ID
2. 扫描 ChromaDB 中的所有文档
3. 找出存在的"孤儿文档"
4. 直接使用 ChromaDB 客户端删除孤儿文档
"""
import sys
import asyncio
from typing import Set, List, Dict
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import chromadb
from sqlalchemy import select
from app.core.db.database import get_async_db_manager
from app.models.sql_graph import File
from app.config import get_settings


async def get_db_file_ids() -> Set[str]:
    """从 PostgreSQL 获取所有有效文件 ID"""
    print("正在从数据库获取有效文件列表...")
    async with get_async_db_manager().session_scope() as session:
        stmt = select(File.id)
        result = await session.execute(stmt)
        file_ids = set(result.scalars().all())
    
    print(f"✅ 数据库中共有 {len(file_ids)} 个有效文件")
    return file_ids


def get_chroma_files(collection) -> Dict[str, dict]:
    """从 ChromaDB 获取所有文件信息"""
    print(f"正在扫描 ChromaDB...")
    
    # 获取默认 Workspace 的所有文档
    results = collection.get(
        where={"workspace_id": {"$eq": "default"}},
        include=["metadatas"]
    )
    
    file_map = {}
    for metadata in results.get("metadatas", []):
        file_id = metadata.get("file_id")
        file_name = metadata.get("source_file")
        
        if file_id:
            if file_id not in file_map:
                file_map[file_id] = {
                    "file_id": file_id,
                    "file_name": file_name,
                    "count": 0
                }
            file_map[file_id]["count"] += 1
            
    print(f"✅ ChromaDB 中共有 {len(file_map)} 个文件")
    return file_map


async def main():
    print(f"\n{'='*60}")
    print("🧹 向量库同步清理工具 (Direct Mode)")
    print(f"{'='*60}\n")
    
    try:
        # 1. 获取有效 ID (Postgres)
        valid_ids = await get_db_file_ids()
        
        # 2. 初始化 Chroma 客户端
        settings = get_settings()
        persist_path = Path(settings.chroma.persist_dir).resolve()
        client = chromadb.PersistentClient(path=str(persist_path))
        collection = client.get_or_create_collection(name="tenant_docs")
        
        # 3. 获取所有 Chroma 文件
        chroma_files = get_chroma_files(collection)
        
        # 4. 找出孤儿文档
        orphans = []
        for file_id, info in chroma_files.items():
            if file_id not in valid_ids:
                orphans.append(info)
        
        # 5. 执行清理
        if not orphans:
            print("\n🎉 未发现孤儿文档，向量库与数据库完全同步！")
            return
            
        print(f"\n⚠️  发现 {len(orphans)} 个孤儿文档:")
        for i, orphan in enumerate(orphans, 1):
            print(f"  {i}. {orphan['file_name']} (ID: {orphan['file_id']}) - {orphan['count']} 切片")
            
        print("\n🤖 自动执行清理...")
        deleted_count = 0
        
        for orphan in orphans:
            try:
                collection.delete(
                    where={"file_id": {"$eq": orphan["file_id"]}}
                )
                print(f"  ✅ 已删除: {orphan['file_name']}")
                deleted_count += 1
            except Exception as e:
                print(f"  ❌ 删除失败: {orphan['file_name']} - {e}")
                
        print(f"\n✨ 清理完成！共删除 {deleted_count} 个文档。")
        
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    asyncio.run(main())
