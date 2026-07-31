"""直接 ChromaDB 查询测试"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.skills.doc_skill import DocSkill

skill = DocSkill()

# 直接查询 ChromaDB（不经过 doc_skill 逻辑）
print("直接 ChromaDB 查询测试")
print("=" * 60)

# 1. 检查所有文档的 workspace_id
results = skill.collection.get(
    include=["metadatas"],
    limit=10
)

print(f"总切片数: {skill.collection.count()}")
print("\n前10个切片的 workspace_id:")
for meta in results.get("metadatas", []):
    ws = meta.get("workspace_id", "NONE")
    src = meta.get("source_file", "")[:30]
    print(f"  workspace_id='{ws}', source={src}")

# 2. 尝试用 workspace_id=default 过滤
print("\n" + "=" * 60)
print("使用 workspace_id='default' 过滤:")
filtered = skill.collection.get(
    where={"workspace_id": {"$eq": "default"}},
    include=["metadatas", "documents"],
    limit=5
)
print(f"匹配数量: {len(filtered.get('metadatas', []))}")

# 3. 尝试全文搜索（无过滤）
print("\n" + "=" * 60)
print("全文搜索 '贾湖骨笛' (无 workspace 过滤):")

from app.core.llm.async_embedding import get_async_embedding
import asyncio

async def search():
    embed = get_async_embedding()
    query_vec = await embed.embed_texts(["贾湖骨笛"])
    
    results = skill.collection.query(
        query_embeddings=query_vec,
        n_results=3,
        include=["metadatas", "documents", "distances"]
    )
    
    print(f"找到 {len(results.get('ids', [[]])[0])} 条结果")
    for i, (doc, meta, dist) in enumerate(zip(
        results.get("documents", [[]])[0],
        results.get("metadatas", [[]])[0],
        results.get("distances", [[]])[0]
    )):
        print(f"\n[{i+1}] distance={dist:.4f}")
        print(f"    workspace_id: {meta.get('workspace_id', '')}")
        print(f"    related_image_ids: {meta.get('related_image_ids', '')}")
        print(f"    content: {doc[:100]}...")

asyncio.run(search())
