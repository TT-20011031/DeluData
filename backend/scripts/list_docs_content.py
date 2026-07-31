"""快速查看知识库文档内容"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.skills.doc_skill import DocSkill

skill = DocSkill()
results = skill.collection.get(
    where={"workspace_id": {"$eq": "default"}},
    include=["metadatas", "documents"],
    limit=30
)

print(f"共 {len(results.get('metadatas', []))} 个切片\n")

# 按文件分组显示
file_map = {}
for i, meta in enumerate(results.get("metadatas", [])):
    source = meta.get("source_file", "unknown")
    file_id = meta.get("file_id", "")
    doc_type = meta.get("type", "text")
    related_imgs = meta.get("related_image_ids", "")
    linked_imgs = meta.get("linked_image_ids", "")
    
    if source not in file_map:
        file_map[source] = {"file_id": file_id, "chunks": [], "images": 0}
    
    content = results["documents"][i][:100] if i < len(results.get("documents", [])) else ""
    file_map[source]["chunks"].append({
        "type": doc_type,
        "content": content,
        "related_imgs": related_imgs,
        "linked_imgs": linked_imgs
    })
    
    if doc_type == "image_summary":
        file_map[source]["images"] += 1

for source, info in file_map.items():
    text_chunks = [c for c in info["chunks"] if c["type"] == "text"]
    img_chunks = [c for c in info["chunks"] if c["type"] == "image_summary"]
    chunks_with_img = sum(1 for c in text_chunks if c["related_imgs"] or c["linked_imgs"])
    
    print(f"📄 {source}")
    print(f"   File ID: {info['file_id']}")
    print(f"   文本切片: {len(text_chunks)}, 图片摘要: {len(img_chunks)}")
    print(f"   有图片关联的切片: {chunks_with_img}")
    
    # 显示第一个文本切片的内容
    if text_chunks:
        print(f"   内容预览: {text_chunks[0]['content']}...")
    print()
