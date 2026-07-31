import pymysql
import chromadb
from pathlib import Path

def debug():
    print("--- Database Check ---")
    try:
        conn = pymysql.connect(host='127.0.0.1', user='root', password='', database='DeluData', charset='utf8mb4')
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, status, workspace_id FROM files WHERE name LIKE '%年终奖%'")
        files = cursor.fetchall()
        print(f"File records found: {files}")
        conn.close()
    except Exception as e:
        print(f"MySQL Error: {e}")

    print("\n--- ChromaDB Check ---")
    try:
        client = chromadb.PersistentClient(path='./data/chroma')
        # collections = client.list_collections()
        # print(f"Collections: {collections}")
        
        collection = client.get_collection('tenant_docs')
        print(f"Collection count: {collection.count()}")
        
        # Search for chunks related to '年终奖' in source_file metadata
        # ChromaDB where filter syntax: {"source_file": {"$contains": "年终奖"}} is not supported for partial match usually, 
        # but let's try exact match if we know the name or just get top items.
        
        results = collection.get(limit=10)
        print(f"Sample ids: {results['ids']}")
        
        # More targeted search in metadata
        # Note: workspace_id mismatch is a common cause.
        # Let's find common workspace_ids.
        sample_metas = results.get('metadatas', [])
        workspaces = set(m.get('workspace_id') for m in sample_metas if m)
        print(f"Common workspaces in sample: {workspaces}")
        
    except Exception as e:
        print(f"ChromaDB Error: {e}")

if __name__ == "__main__":
    debug()
