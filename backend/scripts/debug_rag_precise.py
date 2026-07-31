import pymysql
import chromadb
from pathlib import Path

def debug():
    print("--- Database Check ---")
    try:
        conn = pymysql.connect(host='127.0.0.1', user='root', password='', database='DeluData', charset='utf8mb4')
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, status, workspace_id FROM files WHERE name LIKE %s", ('%年终奖%',))
        files = cursor.fetchall()
        for f in files:
            print(f"File: ID={f[0]}, Name={f[1]}, Status={f[2]}, Workspace={f[3]}")
        conn.close()
    except Exception as e:
        print(f"MySQL Error: {e}")

    print("\n--- ChromaDB Check ---")
    try:
        client = chromadb.PersistentClient(path='./data/chroma')
        collection = client.get_collection('tenant_docs')
        print(f"Collection count: {collection.count()}")
        
        # Try to find the document by partial name in metadata
        # ChromaDB get() allows 'where' filter
        results = collection.get(limit=100)
        found = False
        for i, meta in enumerate(results.get('metadatas', [])):
            if meta and '年终奖' in meta.get('source_file', ''):
                print(f"Found in Chroma: ID={results['ids'][i]}, Meta={meta}")
                found = True
        
        if not found:
            print("No document with '年终奖' in source_file found in first 100 docs.")
            # Print all source files in first 100
            sources = set(m.get('source_file') for m in results.get('metadatas', []) if m)
            print(f"Sample source files: {sources}")
            
        # Get common workspace_id in all docs
        all_metas = results.get('metadatas', [])
        workspaces = set(m.get('workspace_id') for m in all_metas if m)
        print(f"Workspaces in first 100: {workspaces}")
        
    except Exception as e:
        print(f"ChromaDB Error: {e}")

if __name__ == "__main__":
    debug()
