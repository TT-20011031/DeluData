"""
获取 LangGraph 流式处理相关文档
"""
import asyncio
import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md

COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def clean_html_to_markdown(html_content: str) -> str:
    """Extract focus content from Mintlify docs and convert to Markdown."""
    soup = BeautifulSoup(html_content, "html.parser")
    content = soup.find("article") or soup.find("main") or soup.find("body")
    
    for element in content.find_all(["nav", "footer", "script", "style", "aside"]):
        element.decompose()
    
    markdown = md(str(content), heading_style="ATX")
    return markdown.strip()

async def fetch_doc(path: str) -> str:
    base_url = "https://docs.langchain.com/"
    url = f"{base_url}{path.lstrip('/')}"
    
    async with httpx.AsyncClient(headers=COMMON_HEADERS, follow_redirects=True, timeout=30.0) as client:
        response = await client.get(url)
        if response.status_code != 200:
            return f"Error: Failed to fetch {url} (Status: {response.status_code})"
        return clean_html_to_markdown(response.text)

async def main():
    # 获取 LangGraph 流式处理文档
    docs = [
        "oss/python/langgraph/how-to-guides/streaming/streaming-content",
        "oss/python/langgraph/how-to-guides/streaming/streaming-from-subgraphs", 
        "oss/python/langgraph/how-to-guides/streaming/streaming-events-from-within-tools",
    ]
    
    output_file = r"d:\DeLu\DeLuData\DeluData\mcp-langchain\langgraph_streaming_docs.md"
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("# LangGraph 流式处理文档汇总\n\n")
        f.write("_自动从 docs.langchain.com 获取_\n\n")
        f.write("---\n\n")
        
        for doc_path in docs:
            print(f"Fetching: {doc_path}")
            content = await fetch_doc(doc_path)
            f.write(f"## 来源: {doc_path}\n\n")
            f.write(content)
            f.write("\n\n---\n\n")
    
    print(f"Done! Output saved to: {output_file}")

if __name__ == "__main__":
    asyncio.run(main())
