"""检查切片的图片关联元数据"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.skills.doc_skill import DocSkill

skill = DocSkill()

# 查找 937f4926 文件的切片
file_id = "937f4926-6167-4009-ae1a-6078e08c5dc5"
results = skill.collection.get(
    where={"file_id": {"$eq": file_id}},
    include=["metadatas", "documents"],
    limit=50
)

print(f"File ID: {file_id}")
print(f"总切片数: {len(results.get('metadatas', []))}")
print()

for i, meta in enumerate(results.get("metadatas", [])[:10]):
    doc_type = meta.get("type", "text")
    related = meta.get("related_image_ids", "")
    linked = meta.get("linked_image_ids", "")
    header = meta.get("header_path", "")
    content = results["documents"][i][:80] if i < len(results.get("documents", [])) else ""
    
    print(f"[{i+1}] type={doc_type}")
    print(f"    header: {header}")
    print(f"    related_image_ids: '{related}'")
    print(f"    linked_image_ids: '{linked}'")
    print(f"    content: {content}...")
    print()
