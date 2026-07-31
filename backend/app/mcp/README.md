# DeluData Knowledge MCP

This MCP server lets other agents reuse DeluData's knowledge-base pipeline:

- upload documents
- check parsing/vectorization task status
- retrieve RAG evidence for answer generation

Other agents do not choose workspace, department, visibility, or permissions.
Those are fixed by the MCP server operator through environment variables.

## Tools

- `delu_kb_upload_documents`
- `delu_kb_get_tasks`
- `delu_kb_retrieve`
- `delu_kb_ask`

`delu_kb_ask` uses the same `auto` Supervisor workflow as the web chat. It
waits for the generated plan to complete and returns both the final natural
language answer and the file citations that were actually supplied to the
Synthesizer. Use `delu_kb_retrieve` when the caller wants raw evidence instead
of a generated answer.

## Server Configuration

Set these in `backend/.env` or in the process environment:

```env
MCP_KNOWLEDGE_WORKSPACE_ID=default
MCP_KNOWLEDGE_USER_ID=mcp-agent
MCP_KNOWLEDGE_ROLE=admin
MCP_KNOWLEDGE_DEPT_ID=
MCP_KNOWLEDGE_VISIBILITY=dept
MCP_KNOWLEDGE_DEFAULT_TOP_K=5

# Keep false unless the MCP host and DeluData backend are on the same trusted machine.
MCP_KNOWLEDGE_ALLOW_LOCAL_PATHS=false
MCP_KNOWLEDGE_ALLOWED_LOCAL_DIRS=
```

Recommended upload source kinds for external agents:

- `base64`
- `http_url`
- `text`

`local_path` is supported only when `MCP_KNOWLEDGE_ALLOW_LOCAL_PATHS=true`.

## Run

From the repository root:

```bash
cd backend
python -m app.mcp.knowledge_server
```

The backend dependencies must be installed first:

```bash
pip install -r requirements.txt
```

The ingestion worker should be running when `RAG_INGEST_USE_DB_QUEUE=true`:

```bash
python -m app.entrypoints.ingestion_worker
```

## MCP Client Config

Example command-style MCP config:

```json
{
  "mcpServers": {
    "deludata-knowledge": {
      "command": "python",
      "args": ["-m", "app.mcp.knowledge_server"],
      "cwd": "/path/to/DeluData_pro/backend"
    }
  }
}
```

On clients that do not support `cwd`, wrap the command in a shell script that
first changes into the `backend` directory.

## Remote MCP Through Domain

For agents that cannot access the server filesystem, run the MCP server as an
HTTP service behind Nginx.

Set a long random token:

```env
MCP_KNOWLEDGE_TRANSPORT=http
MCP_KNOWLEDGE_HTTP_HOST=0.0.0.0
MCP_KNOWLEDGE_HTTP_PORT=8020
MCP_KNOWLEDGE_REMOTE_TOKEN=replace-with-a-long-random-secret
MCP_KNOWLEDGE_ALLOWED_ORIGINS=https://agent.pro.deluagent.com
```

With `docker-compose.yml`, the `knowledge-mcp` service runs inside the Docker
network and Nginx exposes it at:

```text
https://agent.pro.deluagent.com/mcp/deludata-knowledge/mcp
```

Remote MCP client config:

```json
{
  "mcpServers": {
    "deludata-knowledge": {
      "url": "https://agent.pro.deluagent.com/mcp/deludata-knowledge/mcp",
      "headers": {
        "Authorization": "Bearer replace-with-a-long-random-secret"
      }
    }
  }
}
```

The HTTP MCP endpoint rejects requests when `MCP_KNOWLEDGE_REMOTE_TOKEN` is not
configured.

## Example Calls

Upload text:

```json
{
  "documents": [
    {
      "name": "FAQ.md",
      "source": {
        "kind": "text",
        "mime_type": "text/markdown",
        "content": "# FAQ\n..."
      },
      "metadata": {
        "document_type": "faq",
        "business_domain": "support"
      }
    }
  ]
}
```

Retrieve evidence:

```json
{
  "query": "售后政策里退换货时效是多少？",
  "top_k": 5,
  "deep_search": false,
  "include_images": true
}
```
