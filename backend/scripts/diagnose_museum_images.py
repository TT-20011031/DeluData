"""
博物馆图片诊断脚本

诊断"文物.docx"等文档的图片提取和关联情况
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.skills.doc_skill import DocSkill
from app.models.common.context import UserContext


async def diagnose_document(file_name: str = "文物"):
    """诊断指定文档的图片关联情况"""
    print("\n" + "=" * 60)
    print(f"🔍 诊断文档: {file_name}")
    print("=" * 60 + "\n")
    
    skill = DocSkill()
    
    # 使用 default workspace（与博物馆导览服务一致）
    user_context = UserContext(
        user_id="diagnose_user",
        workspace_id="default",
        role="admin",
        data_scope=1  # ALL
    )
    
    # 1. 查找文档
    print("📄 步骤 1: 查找文档...")
    results = skill.collection.get(
        where={"workspace_id": {"$eq": user_context.workspace_id}},
        include=["metadatas", "documents"]
    )
    
    # 按文件名过滤
    matching_chunks = []
    file_ids = set()
    for i, metadata in enumerate(results.get("metadatas", [])):
        source_file = metadata.get("source_file", "")
        if file_name.lower() in source_file.lower():
            matching_chunks.append({
                "index": i,
                "metadata": metadata,
                "content": results["documents"][i] if i < len(results.get("documents", [])) else ""
            })
            file_ids.add(metadata.get("file_id", ""))
    
    if not matching_chunks:
        print(f"  ❌ 未找到包含 '{file_name}' 的文档")
        print(f"  💡 提示: 请确认文档已上传到知识库")
        
        # 列出所有已有文档
        all_files = set()
        for metadata in results.get("metadatas", []):
            all_files.add(metadata.get("source_file", ""))
        
        print(f"\n  📋 已有文档列表:")
        for f in sorted(all_files):
            print(f"    - {f}")
        return
    
    print(f"  ✅ 找到 {len(matching_chunks)} 个切片，来自 {len(file_ids)} 个文件版本")
    
    # 2. 分析文本切片
    print("\n📝 步骤 2: 分析文本切片...")
    text_chunks = [c for c in matching_chunks if c["metadata"].get("type", "text") == "text"]
    image_chunks = [c for c in matching_chunks if c["metadata"].get("type") == "image_summary"]
    
    print(f"  - 文本切片数: {len(text_chunks)}")
    print(f"  - 图片摘要切片数: {len(image_chunks)}")
    
    # 3. 检查图片关联
    print("\n🖼️  步骤 3: 检查图片关联...")
    chunks_with_images = 0
    all_related_ids = set()
    all_linked_ids = set()
    
    for chunk in text_chunks:
        metadata = chunk["metadata"]
        related = metadata.get("related_image_ids", "")
        linked = metadata.get("linked_image_ids", "")
        
        if related:
            chunks_with_images += 1
            for img_id in related.split(","):
                if img_id.strip():
                    all_related_ids.add(img_id.strip())
        
        if linked:
            for img_id in linked.split(","):
                if img_id.strip():
                    all_linked_ids.add(img_id.strip())
    
    print(f"  - 有关联图片的切片数: {chunks_with_images}/{len(text_chunks)}")
    print(f"  - related_image_ids 总数: {len(all_related_ids)}")
    print(f"  - linked_image_ids 总数 (精确标记): {len(all_linked_ids)}")
    
    if all_related_ids:
        print(f"  - 关联的图片 ID: {sorted(all_related_ids)}")
    if all_linked_ids:
        print(f"  - 精确标记的图片 ID: {sorted(all_linked_ids)}")
    
    # 4. 检查图片摘要
    print("\n📷 步骤 4: 检查图片摘要...")
    if image_chunks:
        print(f"  ✅ 找到 {len(image_chunks)} 个图片摘要")
        for i, chunk in enumerate(image_chunks[:5]):  # 只显示前5个
            img_id = chunk["metadata"].get("image_id", "")
            summary = chunk["content"][:100] + "..." if len(chunk["content"]) > 100 else chunk["content"]
            print(f"\n  [{i+1}] 图片 ID: {img_id}")
            print(f"      摘要: {summary}")
    else:
        print("  ❌ 未找到图片摘要！")
        print("  💡 可能原因:")
        print("     1. 文档中没有嵌入图片")
        print("     2. 图片太小被过滤（最小 50x50 像素）")
        print("     3. VLM 服务未正常工作")
    
    # 5. 显示切片详情
    print("\n📋 步骤 5: 显示部分切片详情...")
    for i, chunk in enumerate(text_chunks[:3]):  # 只显示前3个
        metadata = chunk["metadata"]
        content = chunk["content"][:200] + "..." if len(chunk["content"]) > 200 else chunk["content"]
        
        print(f"\n  [{i+1}] Chunk ID: {metadata.get('chunk_index', '')}")
        print(f"      Header: {metadata.get('header_path', '(无)')}")
        print(f"      related_image_ids: {metadata.get('related_image_ids', '(空)')}")
        print(f"      linked_image_ids: {metadata.get('linked_image_ids', '(空)')}")
        print(f"      内容: {content}")
    
    # 6. 诊断结论
    print("\n" + "=" * 60)
    print("💡 诊断结论")
    print("=" * 60)
    
    if len(image_chunks) == 0:
        print("\n❌ 问题: 文档中没有提取到图片摘要")
        print("   建议: 检查 VLM 服务配置，或重新上传文档")
    elif chunks_with_images == 0:
        print("\n❌ 问题: 图片已提取，但未关联到文本切片")
        print("   可能原因:")
        print("   1. 文档缺少 [IMAGE:001] 等精确标记")
        print("   2. 语义匹配阈值过高 (当前 0.65)")
        print("   3. 图片描述与文本内容语义差异大")
        print("\n   建议解决方案:")
        print("   1. 在文档中添加 [IMAGE:001] 标记")
        print("   2. 或降低配置 RAG_IMAGE_SEMANTIC_THRESHOLD=0.5")
    else:
        print(f"\n✅ 图片关联正常: {chunks_with_images} 个切片有关联图片")
        
        # 检查图片文件是否存在
        print("\n   检查图片文件...")
        from app.config import get_settings
        settings = get_settings()
        
        for file_id in file_ids:
            image_dir = Path(settings.file_storage_path) / "images" / file_id
            if image_dir.exists():
                images = list(image_dir.glob("*.png"))
                print(f"   📁 {file_id}: {len(images)} 张图片文件")
            else:
                print(f"   ❌ {file_id}: 图片目录不存在!")


async def main():
    import sys
    file_name = sys.argv[1] if len(sys.argv) > 1 else "文物"
    await diagnose_document(file_name)


if __name__ == "__main__":
    asyncio.run(main())
