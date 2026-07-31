"""
知识库文档清单诊断脚本

查询 ChromaDB 和 PostgreSQL 数据库中的所有文档
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.skills.doc_skill import DocSkill
from app.models.context import UserContext
from app.core.db.database import get_async_db_manager
from app.models.sql_graph import File
from sqlalchemy import select


async def check_chroma_db():
    """检查 ChromaDB 中的文档"""
    print("\n" + "="*60)
    print("📦 ChromaDB 中的文档")
    print("="*60 + "\n")
    
    skill = DocSkill()
    user_context = UserContext(
        user_id="default_user",
        workspace_id="default",
        allowed_tables=["*"],
        role="admin"
    )
    
    # 获取所有文档的元数据
    results = skill.collection.get(
        where={"workspace_id": {"$eq": user_context.workspace_id}},
        include=["metadatas", "documents"]
    )
    
    # 按文件分组
    file_map = {}
    for i, metadata in enumerate(results.get("metadatas", [])):
        file_id = metadata.get("file_id", "")
        file_name = metadata.get("source_file", "")
        
        if file_id not in file_map:
            file_map[file_id] = {
                "file_name": file_name,
                "chunk_count": 0,
                "total_chars": 0
            }
        
        file_map[file_id]["chunk_count"] += 1
        
        # 计算字符数
        if i < len(results.get("documents", [])):
            doc_content = results["documents"][i]
            file_map[file_id]["total_chars"] += len(doc_content)
    
    print(f"📊 统计信息:")
    print(f"  - 总文件数: {len(file_map)}")
    print(f"  - 总切片数: {len(results.get('metadatas', []))}")
    print()
    
    print(f"📄 文件清单:")
    for i, (file_id, info) in enumerate(file_map.items(), 1):
        print(f"\n  [{i}] {info['file_name']}")
        print(f"      File ID: {file_id}")
        print(f"      切片数: {info['chunk_count']}")
        print(f"      总字符: {info['total_chars']}")
    
    print()


async def check_postgres_db():
    """检查 PostgreSQL 中的文档"""
    print("\n" + "="*60)
    print("🗄️  PostgreSQL 中的文档")
    print("="*60 + "\n")
    
    try:
        async with get_async_db_manager().session_scope() as session:
            stmt = select(File).order_by(File.created_at.desc())
            result = await session.execute(stmt)
            files = result.scalars().all()
            
            print(f"📊 统计信息:")
            print(f"  - 总文件数: {len(files)}")
            print()
            
            print(f"📄 文件清单:")
            for i, file in enumerate(files, 1):
                print(f"\n  [{i}] {file.name}")
                print(f"      ID: {file.id}")
                print(f"      大小: {file.size or 0} bytes")
                print(f"      上传者: {file.uploader_id}")
                print(f"      创建时间: {file.created_at}")
                print(f"      Workspace: {file.workspace_id}")
        
        print()
        
    except Exception as e:
        print(f"  ❌ 查询失败: {e}")
        print()


async def find_duplicates():
    """查找重复文档"""
    print("\n" + "="*60)
    print("🔍 查找重复文档")
    print("="*60 + "\n")
    
    skill = DocSkill()
    user_context = UserContext(
        user_id="default_user",
        workspace_id="default",
        allowed_tables=["*"],
        role="admin"
    )
    
    results = skill.collection.get(
        where={"workspace_id": {"$eq": user_context.workspace_id}},
        include=["metadatas"]
    )
    
    # 按文件名分组
    name_map = {}
    for metadata in results.get("metadatas", []):
        file_name = metadata.get("source_file", "")
        file_id = metadata.get("file_id", "")
        
        if file_name not in name_map:
            name_map[file_name] = []
        
        if file_id not in name_map[file_name]:
            name_map[file_name].append(file_id)
    
    # 找出重复的
    duplicates = {name: ids for name, ids in name_map.items() if len(ids) > 1}
    
    if duplicates:
        print(f"⚠️  发现 {len(duplicates)} 个重复文件名:")
        for name, ids in duplicates.items():
            print(f"\n  文件名: {name}")
            print(f"  重复 ID 数量: {len(ids)}")
            for file_id in ids:
                print(f"    - {file_id}")
    else:
        print("✅ 未发现重复文件名")
    
    print()


async def main():
    """主函数"""
    await check_chroma_db()
    await check_postgres_db()
    await find_duplicates()
    
    print("\n" + "="*60)
    print("💡 诊断建议")
    print("="*60 + "\n")
    print("1. 如果 ChromaDB 和 PostgreSQL 数量不一致:")
    print("   - 可能是数据库同步问题")
    print("   - 建议检查文件上传逻辑")
    print()
    print("2. 如果发现重复文档:")
    print("   - 可能是重复上传导致")
    print("   - 建议删除旧版本文档")
    print()
    print("3. 如果发现测试文档（如 Untitled-*）:")
    print("   - 建议删除测试文档")
    print()


if __name__ == "__main__":
    asyncio.run(main())
