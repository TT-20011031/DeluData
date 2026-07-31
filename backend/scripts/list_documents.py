"""
知识库文档清单查询（同步版本）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import chromadb
from app.config import get_settings


def main():
    """主函数"""
    output_file = Path("document_report.txt")
    
    with open(output_file, "w", encoding="utf-8") as f:
        def log(msg=""):
            print(msg)
            f.write(msg + "\n")
            
        log("\n" + "="*60)
        log("📦 ChromaDB 文档清单")
        log("="*60 + "\n")
        
        settings = get_settings()
        persist_path = Path(settings.chroma.persist_dir).resolve()
        
        client = chromadb.PersistentClient(path=str(persist_path))
        collection = client.get_or_create_collection(
            name="tenant_docs",
            metadata={"description": "User uploaded documents for RAG"}
        )
        
        # 获取所有文档
        results = collection.get(
            where={"workspace_id": {"$eq": "default"}},
            include=["metadatas", "documents"]
        )
        
        # 按文件分组
        file_map = {}
        for i, metadata in enumerate(results.get("metadatas", [])):
            file_id = metadata.get("file_id", "")
            file_name = metadata.get("source_file", "")
            
            if file_id not in file_map:
                file_map[file_id] = {
                    "file_name": file_name,
                    "chunk_count": 0,
                    "total_chars": 0,
                    "first_100_chars": ""
                }
            
            file_map[file_id]["chunk_count"] += 1
            
            # 计算字符数
            if i < len(results.get("documents", [])):
                doc_content = results["documents"][i]
                file_map[file_id]["total_chars"] += len(doc_content)
                
                # 保存第一个切片的前100字符
                if file_map[file_id]["chunk_count"] == 1:
                    file_map[file_id]["first_100_chars"] = doc_content[:100].replace('\n', ' ')
        
        log(f"📊 统计信息:")
        log(f"  - 总文件数: {len(file_map)}")
        log(f"  - 总切片数: {len(results.get('metadatas', []))}")
        log("")
        
        log(f"📄 文件清单 ({len(file_map)} 个文件):")
        log("")
        
        for i, (file_id, info) in enumerate(sorted(file_map.items(), key=lambda x: x[1]['file_name']), 1):
            log(f"{i}. {info['file_name']}")
            log(f"   File ID: {file_id}")
            log(f"   切片数: {info['chunk_count']}, 总字符: {info['total_chars']}")
            log(f"   内容预览: {info['first_100_chars']}...")
            log("")
        
        # 检查重复文件名
        name_count = {}
        for info in file_map.values():
            name = info['file_name']
            name_count[name] = name_count.get(name, 0) + 1
        
        duplicates = {name: count for name, count in name_count.items() if count > 1}
        
        if duplicates:
            log("\n" + "="*60)
            log("⚠️  发现重复文件名")
            log("="*60 + "\n")
            for name, count in duplicates.items():
                log(f"  - {name}: {count} 个不同的 File ID")
            log("")
        
        # 检查可疑文档
        log("\n" + "="*60)
        log("🔍 可疑文档检测")
        log("="*60 + "\n")
        
        suspicious = []
        for file_id, info in file_map.items():
            reasons = []
            
            # 检查是否是 Untitled 文档
            if "Untitled" in info['file_name']:
                reasons.append("文件名包含 'Untitled'（可能是测试文档）")
            
            # 检查文档是否过小
            if info['total_chars'] < 100:
                reasons.append(f"文档过小（{info['total_chars']} 字符）")
            
            # 检查文档是否过大
            if info['chunk_count'] > 100:
                reasons.append(f"切片过多（{info['chunk_count']} 个）")
            
            if reasons:
                suspicious.append((info['file_name'], file_id, reasons))
        
        if suspicious:
            for name, file_id, reasons in suspicious:
                log(f"⚠️  {name}")
                log(f"   File ID: {file_id}")
                for reason in reasons:
                    log(f"   - {reason}")
                log("")
        else:
            log("✅ 未发现可疑文档")
            log("")
            
    print(f"Report written to {output_file.absolute()}")


if __name__ == "__main__":
    main()
