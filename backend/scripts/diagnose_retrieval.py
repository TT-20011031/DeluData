"""
RAG 检索诊断脚本

用于诊断"年终奖怎么发"检索到无关文档的问题

使用方法：
    python scripts/diagnose_retrieval.py "年终奖怎么发"
"""
import asyncio
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.skills.doc_skill import DocSkill
from app.models.context import UserContext
from app.config import get_settings


async def diagnose_query(query: str = "年终奖怎么发"):
    """诊断检索流程"""
    
    print(f"\n{'='*60}")
    print(f"🔍 诊断查询：{query}")
    print(f"{'='*60}\n")
    
    # 创建用户上下文
    user_context = UserContext(
        user_id="default_user",
        workspace_id="default",
        allowed_tables=["*"],
        role="admin"
    )
    
    skill = DocSkill()
    
    # ========== 步骤 1: 查询改写 ==========
    print("📝 步骤 1: 查询改写")
    print("-" * 60)
    
    queries = await skill.query_rewriter.rewrite(query, num_variants=3)
    for i, q in enumerate(queries, 1):
        print(f"  变体 {i}: {q}")
    print()
    
    # ========== 步骤 2: 检查知识库文档 ==========
    print("📚 步骤 2: 知识库文档列表")
    print("-" * 60)
    
    results = skill.collection.get(
        where={"workspace_id": {"$eq": user_context.workspace_id}},
        include=["metadatas"]
    )
    
    file_map = {}
    for metadata in results.get("metadatas", []):
        file_id = metadata.get("file_id", "")
        file_name = metadata.get("source_file", "")
        if file_id and file_id not in file_map:
            file_map[file_id] = file_name
    
    print(f"  共 {len(file_map)} 个文件:")
    for file_id, file_name in file_map.items():
        print(f"    - {file_name} (ID: {file_id[:8]}...)")
    print()
    
    # ========== 步骤 3: 混合检索（Top-50） ==========
    print("🔎 步骤 3: 混合检索 (Dense + Sparse)")
    print("-" * 60)
    
    settings = get_settings()
    candidates = await skill.retriever.search(
        queries, 
        user_context, 
        settings.rag.merged_top_n
    )
    
    print(f"  检索到 {len(candidates)} 个候选:")
    for i, chunk in enumerate(candidates[:10], 1):
        print(f"\n  [{i}] 文件: {chunk.source_file}")
        print(f"      分数: {chunk.score:.4f}")
        print(f"      内容预览: {chunk.content[:100]}...")
    
    if len(candidates) > 10:
        print(f"\n  ... 还有 {len(candidates) - 10} 个候选未显示")
    print()
    
    # ========== 步骤 4: 重排序（Top-5） ==========
    print("🎯 步骤 4: 重排序 (Reranker)")
    print("-" * 60)
    
    try:
        reranked = await skill.reranker.rerank(query, candidates, top_k=5)
        
        print(f"  重排序后 Top-5:")
        for i, chunk in enumerate(reranked, 1):
            print(f"\n  [{i}] 文件: {chunk.source_file}")
            print(f"      Rerank分数: {chunk.rerank_score:.4f}")
            print(f"      向量分数: {chunk.score:.4f}")
            print(f"      内容预览: {chunk.content[:100]}...")
        
    except Exception as e:
        print(f"  ⚠️ 重排序失败: {e}")
        print(f"  降级使用向量相似度排序")
    
    print()
    
    # ========== 诊断建议 ==========
    print("💡 诊断建议")
    print("-" * 60)
    
    # 检查是否有"Untitled"文档
    has_untitled = any("Untitled" in name for name in file_map.values())
    if has_untitled:
        print("  ⚠️ 检测到 'Untitled' 文档，建议：")
        print("     1. 检查该文档内容是否有效")
        print("     2. 如果是空文档或测试文档，建议删除")
        print()
    
    # 检查是否有年终奖相关文档
    has_bonus = any("年终奖" in name for name in file_map.values())
    if not has_bonus:
        print("  ⚠️ 未发现包含'年终奖'的文档名，建议：")
        print("     1. 上传包含年终奖相关信息的文档")
        print("     2. 检查现有文档内容是否包含年终奖信息")
        print()
    
    # 检查 Rerank API 是否工作
    if candidates and not hasattr(candidates[0], 'rerank_score'):
        print("  ⚠️ Rerank API 可能未正常工作，建议：")
        print("     1. 检查阿里云 API Key 配置")
        print("     2. 查看后端日志确认 Rerank 调用状态")
        print()
    
    print()


async def diagnose_specific_doc(doc_name: str = "Untitled-2"):
    """诊断特定文档"""
    
    print(f"\n{'='*60}")
    print(f"📄 诊断文档：{doc_name}")
    print(f"{'='*60}\n")
    
    user_context = UserContext(
        user_id="default_user",
        workspace_id="default",
        allowed_tables=["*"],
        role="admin"
    )
    
    skill = DocSkill()
    
    # 获取文档的所有切片
    results = skill.collection.get(
        where={
            "$and": [
                {"workspace_id": {"$eq": user_context.workspace_id}},
                {"source_file": {"$eq": doc_name}}
            ]
        },
        include=["documents", "metadatas"]
    )
    
    chunks = results.get("documents", [])
    metadatas = results.get("metadatas", [])
    
    if not chunks:
        print(f"  ❌ 未找到文档 '{doc_name}'")
        return
    
    print(f"  文档切片数量: {len(chunks)}")
    print()
    
    total_length = sum(len(chunk) for chunk in chunks)
    print(f"  总字符数: {total_length}")
    print()
    
    print("  切片内容预览:")
    for i, (chunk, metadata) in enumerate(zip(chunks[:3], metadatas[:3]), 1):
        print(f"\n  切片 {i}:")
        print(f"    字符数: {len(chunk)}")
        print(f"    内容: {chunk[:200]}...")
    
    if len(chunks) > 3:
        print(f"\n  ... 还有 {len(chunks) - 3} 个切片未显示")
    
    print()
    
    # 诊断
    if total_length < 100:
        print("  ⚠️ 文档内容过少，可能是空文档或测试文档")
        print("     建议删除该文档")
    elif "年终奖" not in "".join(chunks):
        print("  ⚠️ 文档不包含'年终奖'关键词")
        print("     但由于向量语义相似度，仍可能被召回")
        print("     建议上传包含年终奖明确信息的文档")
    
    print()


async def main():
    """主函数"""
    query = sys.argv[1] if len(sys.argv) > 1 else "年终奖怎么发"
    
    # 诊断查询流程
    await diagnose_query(query)
    
    # 诊断 Untitled-2 文档
    await diagnose_specific_doc("Untitled-2")


if __name__ == "__main__":
    asyncio.run(main())
