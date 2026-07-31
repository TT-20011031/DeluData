"""调试 DocSkill 完整流程"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.skills.doc_skill import DocSkill
from app.models.common.context import UserContext
from app.config import get_settings


async def debug():
    skill = DocSkill()
    settings = get_settings()
    
    user_context = UserContext(
        user_id="museum_visitor",
        workspace_id="default",
        role="visitor",
        data_scope=1
    )
    
    query = "贾湖骨笛"
    print(f"Query: {query}")
    print(f"User: {user_context.user_id}, workspace: {user_context.workspace_id}, data_scope: {user_context.data_scope}")
    
    # 1. 验证 user_context
    valid = skill.validate_user_context(user_context)
    print(f"\n1. validate_user_context: {valid}")
    
    # 2. Query Rewrite
    print("\n2. Query Rewrite...")
    queries = await skill.query_rewriter.rewrite(query, num_variants=3)
    print(f"   Queries: {queries}")
    
    # 3. Hybrid Search
    print("\n3. Hybrid Search...")
    merged_top_n = settings.rag.merged_top_n
    print(f"   merged_top_n: {merged_top_n}")
    
    candidates = await skill.retriever.search(queries, user_context, merged_top_n)
    print(f"   Candidates: {len(candidates)}")
    
    for i, c in enumerate(candidates[:3]):
        print(f"   [{i+1}] score={c.score:.3f}, content={c.content[:50]}...")
    
    if not candidates:
        print("   ❌ No candidates! Stopping here.")
        return
    
    # 4. Rerank
    print("\n4. Rerank...")
    reranked = await skill.reranker.rerank(query, candidates, top_k=5)
    print(f"   Reranked: {len(reranked)}")
    
    for i, r in enumerate(reranked):
        score = getattr(r, 'rerank_score', 0) or 0
        print(f"   [{i+1}] rerank_score={score:.3f}, content={r.content[:50]}...")
    
    # 5. Score Filter
    print("\n5. Score Filter...")
    score_threshold = settings.rag.rerank_score_threshold
    print(f"   threshold: {score_threshold}")
    
    filtered = [doc for doc in reranked if (getattr(doc, 'rerank_score', 0) or 0) >= score_threshold]
    print(f"   After filter: {len(filtered)}")
    
    if not filtered:
        print("   ❌ All filtered out! This is the problem.")
        print(f"   Scores were: {[getattr(r, 'rerank_score', 0) for r in reranked]}")


asyncio.run(debug())
