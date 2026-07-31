"""
向量库同步清理脚本

目标：
1. 读取 PostgreSQL 数据库中的所有有效文件 ID
2. 扫描 ChromaDB 中的所有文档
3. 找出存在的"孤儿文档"（在 ChromaDB 中存在但数据库中不存在）
4. 删除这些孤儿文档
"""
import sys
import asyncio
from typing import Set, List
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from app.core.db.database import get_async_db_manager
from app.models.sql_graph import File
from app.skills.doc_skill import DocSkill
from app.models.context import UserContext
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


async def get_chroma_files(skill: DocSkill, workspace_id: str) -> List[dict]:
    """从 ChromaDB 获取所有文件信息"""
    print(f"正在扫描 ChromaDB (workspace: {workspace_id})...")
    
    results = skill.collection.get(
        where={"workspace_id": {"$eq": workspace_id}},
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
            
    files = list(file_map.values())
    print(f"✅ ChromaDB 中共有 {len(files)} 个文件 (包含重复/旧版本)")
    return files


async def main():
    print(f"\n{'='*60}")
    print("🧹 向量库同步清理工具")
    print(f"{'='*60}\n")
    
    try:
        # 1. 获取有效 ID
        valid_ids = await get_db_file_ids()
        
        # 2. 初始化 Chroma 客户端
        skill = DocSkill()
        user_context = UserContext(
            user_id="admin",
            workspace_id="default",  # 假设都在 default 空间，实际需遍历
            allowed_tables=["*"],
            role="admin"
        )
        
        # 3. 获取所有 Chroma 文件
        chroma_files = await get_chroma_files(skill, user_context.workspace_id)
        
        # 4. 找出孤儿文档
        orphans = []
        for f in chroma_files:
            if f["file_id"] not in valid_ids:
                orphans.append(f)
        
        # 5. 执行清理
        if not orphans:
            print("\n🎉 未发现孤儿文档，向量库与数据库完全同步！")
            return
            
        print(f"\n⚠️  发现 {len(orphans)} 个孤儿文档 (数据库中已删除，但向量库仍存在):")
        for i, orphan in enumerate(orphans, 1):
            print(f"  {i}. {orphan['file_name']} (ID: {orphan['file_id']}) - {orphan['count']} 切片")
            
        # 简单参数检查
        import sys
        if "--auto-confirm" in sys.argv:
            print("\n🤖 自动确认模式：执行删除...")
        else:
            confirm = input("\n❓ 是否立即删除这些文档？(y/n): ")
            if confirm.lower() != 'y':
                print("操作已取消")
                return
            
        print("\n🗑️  正在执行删除...")
        deleted_count = 0
        
        for orphan in orphans:
            success = await skill.delete_document(orphan["file_id"], user_context)
            if success:
                print(f"  ✅ 已删除: {orphan['file_name']}")
                deleted_count += 1
            else:
                print(f"  ❌ 删除失败: {orphan['file_name']}")
                
        print(f"\n✨ 清理完成！共删除 {deleted_count} 个文档。")
        
        # 6. 重新生成并验证文档清单
        print("\n🔄 重新验证文档状态...")
        chroma_files_after = await get_chroma_files(skill, user_context.workspace_id)
        print(f"当前 ChromaDB 文件数: {len(chroma_files_after)}")
        
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # 配置 asyncio 策略以避免 Windows 上的 runtime error
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    asyncio.run(main())
