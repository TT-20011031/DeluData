import asyncio
from server import fetch_langchain_doc, fetch_langgraph_doc, search_docs

async def test():
    print("--- Testing search_docs('agent') ---")
    search_res = await search_docs("agent")
    print(search_res)
    
    print("\n--- Testing fetch_langchain_doc('oss/python/langchain/overview') ---")
    doc_res = await fetch_langchain_doc("oss/python/langchain/overview")
    print(doc_res[:500] + "...")

    print("\n--- Testing fetch_langgraph_doc('oss/python/langgraph/overview') ---")
    graph_res = await fetch_langgraph_doc("oss/python/langgraph/overview")
    print(graph_res[:500] + "...")

if __name__ == "__main__":
    asyncio.run(test())
