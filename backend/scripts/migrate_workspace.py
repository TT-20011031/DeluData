import pymysql
import chromadb
from pathlib import Path

def migrate():
    print("--- MySQL Migration ---")
    try:
        conn = pymysql.connect(host='127.0.0.1', user='root', password='', database='DeluData', charset='utf8mb4')
        cursor = conn.cursor()
        
        # Update files
        cursor.execute("UPDATE files SET workspace_id = 'default' WHERE workspace_id = 'default_workspace'")
        print(f"Updated {cursor.rowcount} files records in MySQL.")
        
        # Update folders
        cursor.execute("UPDATE folders SET workspace_id = 'default' WHERE workspace_id = 'default_workspace'")
        print(f"Updated {cursor.rowcount} folders records in MySQL.")
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"MySQL Migration Error: {e}")

    print("\n--- ChromaDB Migration ---")
    try:
        client = chromadb.PersistentClient(path='./data/chroma')
        collection = client.get_collection('tenant_docs')
        
        # In ChromaDB, we can't easily bulk update metadata 'where' workspace_id = 'default_workspace'.
        # We need to fetch and then update.
        results = collection.get(where={"workspace_id": "default_workspace"}, include=["metadatas"])
        
        if results['ids']:
            ids = results['ids']
            metadatas = results['metadatas']
            for meta in metadatas:
                meta['workspace_id'] = 'default'
            
            # ChromaDB update() updates metadata by ID
            collection.update(ids=ids, metadatas=metadatas)
            print(f"Updated {len(ids)} items in ChromaDB.")
        else:
            print("No items with 'default_workspace' found in ChromaDB.")
            
    except Exception as e:
        print(f"ChromaDB Migration Error: {e}")

if __name__ == "__main__":
    migrate()
