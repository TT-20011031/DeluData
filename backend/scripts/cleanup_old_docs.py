"""清理旧文档脚本"""
import chromadb
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

# 要删除的文档名列表
DELETE_FILES = [
    "1443e410-cb87-46bd-8d4a-d6c6052917a9.docx",
]

def cleanup():
    client = chromadb.PersistentClient(path="data/chroma")
    
    collections = client.list_collections()
    print(f"可用 collections: {[c.name for c in collections]}")
    
    if not collections:
        print("没有找到任何 collection")
        return
    
    collection = collections[0]
    
    results = collection.get(include=["metadatas"])
    
    to_delete = []
    for i, meta in enumerate(results["metadatas"]):
        source = meta.get("source_file", "")
        if source in DELETE_FILES or source.endswith(".txt"):
            doc_id = results["ids"][i]
            to_delete.append(doc_id)
            print(f"  将删除: {source}")
    
    if to_delete:
        collection.delete(ids=to_delete)
        print(f"\n已删除 {len(to_delete)} 个 txt 文档切片")
    else:
        print("没有找到 txt 文档")

if __name__ == "__main__":
    cleanup()
