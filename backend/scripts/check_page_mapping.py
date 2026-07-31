"""检查 ChromaDB 中的文档名"""
import chromadb
from pathlib import Path
import sys
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))

def check():
    client = chromadb.PersistentClient(path="data/chroma")
    collection = client.get_collection("tenant_docs")
    
    # 获取所有文档
    results = collection.get(include=["metadatas"])
    
    print(f"找到 {len(results['ids'])} 个切片\n")
    
    # 统计 source_file 名称
    source_files = Counter()
    for meta in results["metadatas"]:
        source = meta.get("source_file", "未知")
        source_files[source] += 1
    
    print("=== 文档名列表 ===")
    for name, count in source_files.most_common():
        print(f"  {name}: {count} 个切片")
    
    # 分析 text chunk 的页码和图片关联
    print("=== 文本切片页码分析 ===")
    for i, meta in enumerate(results["metadatas"]):
        if meta.get("type") == "text":
            header = meta.get("header_path", "")[:30]
            page_numbers = meta.get("page_numbers", "")
            related = meta.get("related_image_ids", "")
            print(f"  {header:<30} | pages={page_numbers:<10} | images={related}")
    
    print("\n=== 图片摘要页码分析 ===")
    for i, meta in enumerate(results["metadatas"]):
        if meta.get("type") == "image_summary":
            img_id = meta.get("image_id", "")
            page = meta.get("page_number", "")
            print(f"  图片 {img_id:<5} | page={page}")

if __name__ == "__main__":
    check()
