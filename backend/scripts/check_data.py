import pymysql
import chromadb
from pathlib import Path

def check_all():
    print("=== MySQL: All Files ===")
    try:
        conn = pymysql.connect(host='127.0.0.1', user='root', password='', database='DeluData', charset='utf8mb4')
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, status, workspace_id, folder_id FROM files")
        rows = cursor.fetchall()
        for r in rows:
            print(f"[{r[2]}] ID: {r[0]}, Name: {r[1]}, Workspace: {r[3]}, Folder: {r[4]}")
        conn.close()
    except Exception as e:
        print(f"MySQL Error: {e}")

    print("\n=== ChromaDB: Collection Check ===")
    try:
        client = chromadb.PersistentClient(path='./data/chroma')
        collection = client.get_collection('tenant_docs')
        print(f"Total count: {collection.count()}")
        
        # Peek at first few documents
        peek = collection.peek(limit=5)
        print("Peek IDs:", peek['ids'])
        metas = peek['metadatas']
        for i, m in enumerate(metas):
            print(f"  Chunk {i} Meta: {m}")
            
    except Exception as e:
        print(f"ChromaDB Error: {e}")

if __name__ == "__main__":
    check_all()
