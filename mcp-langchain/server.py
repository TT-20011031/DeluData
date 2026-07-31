import asyncio
import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("LangChain-Docs")

COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

_SITEMAP_CACHE = []

async def _fetch_sitemap():
    global _SITEMAP_CACHE
    if _SITEMAP_CACHE:
        return _SITEMAP_CACHE
    
    url = "https://docs.langchain.com/sitemap.xml"
    async with httpx.AsyncClient(headers=COMMON_HEADERS, follow_redirects=True, timeout=30.0) as client:
        try:
            response = await client.get(url)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "xml")
                _SITEMAP_CACHE = [loc.text for loc in soup.find_all("loc")]
        except Exception:
            pass
    return _SITEMAP_CACHE

def clean_html_to_markdown(html_content: str) -> str:
    """Extract focus content from Mintlify docs and convert to Markdown."""
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Mintlify main content is usually in <article> or <main>
    content = soup.find("article") or soup.find("main")
    if not content:
        # Fallback to body but try to exclude nav/footer if possible
        content = soup.find("body")

    # Remove unwanted elements
    for element in content.find_all(["nav", "footer", "script", "style", "aside"]):
        element.decompose()

    # Convert to markdown
    markdown = md(str(content), heading_style="ATX")
    return markdown.strip()

@mcp.tool()
async def fetch_langchain_doc(path: str) -> str:
    """
    Fetch and read a page from the LangChain Python documentation.
    :param path: The relative path to the doc, e.g., 'oss/python/langchain/overview'
    """
    base_url = "https://docs.langchain.com/"
    url = f"{base_url}{path.lstrip('/')}"
    
    async with httpx.AsyncClient(headers=COMMON_HEADERS, follow_redirects=True, timeout=30.0) as client:
        response = await client.get(url)
        if response.status_code != 200:
            return f"Error: Failed to fetch {url} (Status: {response.status_code})"
        
        return clean_html_to_markdown(response.text)

@mcp.tool()
async def fetch_langgraph_doc(path: str) -> str:
    """
    Fetch and read a page from the LangGraph Python documentation.
    :param path: The relative path to the doc, e.g., 'oss/python/langgraph/overview'
    """
    # LangGraph is often under the same domain but linked differently
    base_url = "https://docs.langchain.com/"
    url = f"{base_url}{path.lstrip('/')}"
    
    async with httpx.AsyncClient(headers=COMMON_HEADERS, follow_redirects=True, timeout=30.0) as client:
        response = await client.get(url)
        if response.status_code != 200:
            return f"Error: Failed to fetch {url} (Status: {response.status_code})"
        
        return clean_html_to_markdown(response.text)

@mcp.tool()
async def search_docs(query: str) -> str:
    """
    Search for documents in LangChain/LangGraph by keyword.
    """
    urls = await _fetch_sitemap()
    if not urls:
        return "Search failed: Could not fetch sitemap. Please try direct fetch with fetch_langchain_doc/fetch_langgraph_doc."

    query = query.lower()
    matches = [url for url in urls if query in url.lower()]
    
    if not matches:
        return f"No matches found for '{query}'."
    
    # Return top 20 results to keep context manageable
    results = matches[:20]
    output = f"Found {len(matches)} matches (showing top {len(results)}):\n"
    for r in results:
        # Simplify URL to path
        path = r.replace("https://docs.langchain.com/", "")
        output += f"- {path} ({r})\n"
    return output

if __name__ == "__main__":
    mcp.run()
