"""测试检索并查看图片关联"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.skills.doc_skill import DocSkill
from app.models.common.context import UserContext


async def test_search(query: str = "彩绘小狗"):
    """测试检索"""
    print(f"\n查询: {query}")
    print("=" * 60)
    
    skill = DocSkill()
    user_context = UserContext(
        user_id="museum_visitor",
        workspace_id="default",  # 与博物馆服务一致
        role="visitor",
        data_scope=1
    )
    
    results = await skill.query_knowledge_base(
        query=query,
        user_context=user_context,
        top_k=5
    )
    
    print(f"检索到 {len(results)} 条结果\n")
    
    for i, r in enumerate(results):
        print(f"[{i+1}] score={r.score:.3f}")
        print(f"    file_id: {r.metadata.get('file_id', '')}")
        print(f"    related_image_ids: '{r.metadata.get('related_image_ids', '')}'")
        print(f"    linked_image_ids: '{r.metadata.get('linked_image_ids', '')}'")
        print(f"    content: {r.content[:100]}...")
        print()
    
    # 模拟 _extract_images 逻辑
    print("=" * 60)
    print("模拟图片提取:")
    
    images = []
    seen = set()
    for r in results:
        metadata = r.metadata or {}
        file_id = metadata.get("file_id")
        if not file_id:
            continue
        
        related_ids_str = metadata.get("related_image_ids", "")
        linked_ids_str = metadata.get("linked_image_ids", "")
        
        all_image_ids = set()
        if related_ids_str:
            all_image_ids.update(i.strip() for i in related_ids_str.split(",") if i.strip())
        if linked_ids_str:
            all_image_ids.update(i.strip() for i in linked_ids_str.split(",") if i.strip())
        
        caption = metadata.get("title", "") or metadata.get("header_path", "") or ""
        
        for image_id in all_image_ids:
            key = f"{file_id}:{image_id}"
            if key not in seen:
                seen.add(key)
                images.append({
                    "fileId": file_id,
                    "imageId": image_id,
                    "caption": caption
                })
    
    print(f"提取到 {len(images)} 张图片:")
    for img in images[:6]:
        print(f"  - {img['fileId']}/{img['imageId']}.png (caption: {img['caption']})")


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "彩绘小狗"
    asyncio.run(test_search(query))
