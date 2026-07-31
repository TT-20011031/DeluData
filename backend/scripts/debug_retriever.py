"""调试 HybridRetriever"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.rag.hybrid_retriever import get_hybrid_retriever
from app.models.common.context import UserContext


async def debug():
    retriever = get_hybrid_retriever()
    
    user_context = UserContext(
        user_id="museum_visitor",
        workspace_id="default",
        role="visitor",
        data_scope=1  # ALL
    )
    
    print(f"User Context:")
    print(f"  workspace_id: {user_context.workspace_id}")
    print(f"  data_scope: {user_context.data_scope}")
    print(f"  dept_id: {user_context.dept_id}")
    
    # 1. 检查权限过滤器
    permission_filter, allowed_depts = await retriever._build_permission_filter(user_context)
    print(f"\nPermission Filter: {permission_filter}")
    print(f"Allowed Depts: {allowed_depts}")
    
    # 2. 执行检索
    queries = ["贾湖骨笛"]
    print(f"\nSearching for: {queries}")
    
    results = await retriever.search(queries, user_context, top_n=5)
    print(f"\nResults: {len(results)}")
    
    for i, r in enumerate(results):
        print(f"  [{i+1}] score={r.score:.3f}, file={r.metadata.get('source_file', '')[:30]}")
        print(f"       related_image_ids: {r.metadata.get('related_image_ids', '')}")


asyncio.run(debug())
