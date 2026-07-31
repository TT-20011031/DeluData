# MCP Ask Request Top-K Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose `top_k` on `delu_kb_ask` with default 20 and apply it only to that MCP request's retrieval and final text-evidence cap.

**Architecture:** The MCP tool validates and forwards `top_k` through `KnowledgeMcpService` into an explicit optional `ChatService.start_new_session` runtime parameter. ChatService writes the validated value into the turn's internal `user_context`; Doc Tool reads it to override both RAG retrieval count and final text-evidence count. Web chat does not pass the runtime parameter and therefore keeps existing behavior.

**Tech Stack:** Python 3.12, FastAPI service layer, FastMCP, LangGraph supervisor state, pytest/unittest.

## Global Constraints

- Only `delu_kb_ask` requests receive the override.
- `top_k` defaults to 20 and is clamped to `1..20`; values below 1 fall back to 20.
- Do not change global RAG settings, PDF ingestion, vectors, file-name matching, ranking, or `delu_kb_retrieve`.
- Existing MCP callers that omit `top_k` remain compatible.
- The mounted project has no `.git` metadata; do not claim a commit was created.

---

### Task 1: Lock the MCP Tool and Service Contract

**Files:**
- Modify: `backend/tests/test_knowledge_mcp_ask_consistency.py`
- Modify: `backend/app/mcp/knowledge_server.py:314-336`
- Modify: `backend/app/services/knowledge_mcp_service.py:409-466`
- Modify: `backend/app/services/chat_service.py:82-148`

**Interfaces:**
- Consumes: Existing `delu_kb_ask(query, reply_model_key, deep_search, session_id)` contract.
- Produces: `delu_kb_ask(..., top_k: int = 20)` and `KnowledgeMcpService.ask(..., top_k: int = 20)`.
- Produces: `ChatService.start_new_session(..., mcp_ask_evidence_top_k: Optional[int] = None)`.

- [ ] **Step 1: Write failing service tests**

Add assertions to `test_knowledge_mcp_ask_consistency.py`:

```python
async def test_ask_injects_default_mcp_evidence_top_k(self):
    captured = {}

    async def fake_start(_self, **kwargs):
        captured.update(kwargs)
        return {
            "session_id": "s1",
            "plan_id": "p1",
            "steps": [],
            "status": "completed",
            "message": "ok",
        }

    async def fake_plan(_self, _session_id):
        return {"status": "completed", "execution_results": []}

    with (
        patch("app.services.chat_service.ChatService.start_new_session", new=fake_start),
        patch("app.services.chat_service.ChatService.get_session_plan", new=fake_plan),
    ):
        await _service().ask(query="q")

    self.assertEqual(captured["mcp_ask_evidence_top_k"], 20)


async def test_ask_clamps_request_top_k(self):
    captured = []

    async def fake_start(_self, **kwargs):
        captured.append(kwargs["mcp_ask_evidence_top_k"])
        return {
            "session_id": "s1",
            "plan_id": "p1",
            "steps": [],
            "status": "completed",
            "message": "ok",
        }

    async def fake_plan(_self, _session_id):
        return {"status": "completed", "execution_results": []}

    with (
        patch("app.services.chat_service.ChatService.start_new_session", new=fake_start),
        patch("app.services.chat_service.ChatService.get_session_plan", new=fake_plan),
    ):
        await _service().ask(query="q", top_k=8)
        await _service().ask(query="q", top_k=99)
        await _service().ask(query="q", top_k=0)

    self.assertEqual(captured, [8, 20, 20])
```

- [ ] **Step 2: Run the tests and verify RED**

Run from `backend`:

```powershell
python -m pytest tests/test_knowledge_mcp_ask_consistency.py `
  -k "injects_default_mcp_evidence_top_k or clamps_request_top_k" -q
```

Expected: FAIL because `KnowledgeMcpService.ask` does not accept `top_k` and ChatService does not receive `mcp_ask_evidence_top_k`.

- [ ] **Step 3: Implement the minimal MCP contract**

In `knowledge_server.py`:

```python
async def delu_kb_ask(
    query: str,
    reply_model_key: Optional[Literal["flash", "plus", "max"]] = None,
    deep_search: bool = False,
    session_id: Optional[str] = None,
    top_k: int = 20,
) -> dict[str, Any]:
    payload = await _service().ask(
        query=query,
        reply_model_key=reply_model_key,
        deep_search=deep_search,
        session_id=session_id,
        top_k=top_k,
    )
```

In `KnowledgeMcpService.ask`:

```python
raw_top_k = int(top_k or 20)
safe_top_k = 20 if raw_top_k < 1 else min(raw_top_k, 20)

result = await chat_service.start_new_session(
    ...,
    mcp_ask_evidence_top_k=safe_top_k,
)
```

In `ChatService.start_new_session`, add the optional internal runtime parameter and inject it only when supplied:

```python
mcp_ask_evidence_top_k: Optional[int] = None,
...
if mcp_ask_evidence_top_k is not None:
    user_context_dict["mcp_ask_evidence_top_k"] = int(mcp_ask_evidence_top_k)
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_knowledge_mcp_ask_consistency.py `
  -k "injects_default_mcp_evidence_top_k or clamps_request_top_k or ask_runs_auto" -q
```

Expected: PASS.

---

### Task 2: Apply the Override Only Inside Doc Tool

**Files:**
- Modify: `backend/app/tests/test_doc_worker_routing.py`
- Modify: `backend/app/tools/doc_tool.py:266-390`
- Modify: `backend/app/tools/doc_tool.py:582-601`

**Interfaces:**
- Consumes: `user_context["mcp_ask_evidence_top_k"]` as an internal optional integer.
- Produces: Request-scoped RAG retrieval and final text evidence count using the same safe value.
- Preserves: Existing unmarked web/default path.

- [ ] **Step 1: Write failing Doc Tool isolation tests**

Add two tests to `TestRunDocTaskRouting`:

```python
async def test_mcp_ask_top_k_controls_retrieval_and_final_evidence(self, monkeypatch):
    from app.tools.doc_tool import run_doc_task

    captured = {}
    chunks = [
        _make_doc_chunk(content=f"证据{i}", chunk_id=f"c{i}", score=0.9, file_id=None)
        for i in range(20)
    ]

    async def fake_query_knowledge_base(*args, **kwargs):
        captured["top_k"] = kwargs["top_k"]
        return chunks

    skill_stub = MagicMock()
    skill_stub.query_knowledge_base = fake_query_knowledge_base
    skill_stub.wiki_navigate = AsyncMock()
    _install_mock_skill(monkeypatch, skill_stub)

    result = await run_doc_task(
        query="打印机历史价格",
        user_context={
            **_make_user_context_dict(),
            "mcp_ask_evidence_top_k": 20,
        },
        knowledge_path="rag",
        doc_scope={"include_images": False},
    )

    assert captured["top_k"] == 20
    assert len(result.meta["final_context"]) == 20


async def test_unmarked_request_keeps_default_final_evidence_cap(self, monkeypatch):
    from app.tools.doc_tool import run_doc_task

    chunks = [
        _make_doc_chunk(content=f"证据{i}", chunk_id=f"c{i}", score=0.9, file_id=None)
        for i in range(20)
    ]
    skill_stub = MagicMock()
    skill_stub.query_knowledge_base = AsyncMock(return_value=chunks)
    skill_stub.wiki_navigate = AsyncMock()
    _install_mock_skill(monkeypatch, skill_stub)

    result = await run_doc_task(
        query="打印机历史价格",
        user_context=_make_user_context_dict(),
        knowledge_path="rag",
        doc_scope={"include_images": False},
    )

    assert len(result.meta["final_context"]) == 5
```

- [ ] **Step 2: Run the tests and verify RED**

Run from `backend`:

```powershell
python -m pytest app/tests/test_doc_worker_routing.py `
  -k "mcp_ask_top_k or unmarked_request_keeps_default" -q
```

Expected: MCP-marked test FAILS with retrieval `top_k=15` and/or final context length 5; unmarked test PASSES.

- [ ] **Step 3: Implement request-scoped selection**

In `run_doc_task`:

```python
raw_mcp_top_k = raw_user_context.get("mcp_ask_evidence_top_k")
mcp_ask_evidence_top_k = None
if raw_mcp_top_k is not None:
    try:
        parsed = int(raw_mcp_top_k)
    except (TypeError, ValueError):
        parsed = 20
    mcp_ask_evidence_top_k = 20 if parsed < 1 else min(parsed, 20)
```

Use it for retrieval:

```python
fetch_top_k = (
    mcp_ask_evidence_top_k
    if mcp_ask_evidence_top_k is not None
    else max(top_k, settings.rag.rerank_top_k)
)
```

Use it for final text evidence:

```python
text_max_chunks = (
    mcp_ask_evidence_top_k
    if mcp_ask_evidence_top_k is not None
    else max(1, int(getattr(settings.rag, "synth_text_evidence_cap", 5)))
)
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest app/tests/test_doc_worker_routing.py `
  -k "mcp_ask_top_k or unmarked_request_keeps_default" -q
```

Expected: 2 passed.

---

### Task 3: Regression and Handoff Verification

**Files:**
- Verify: `backend/tests/test_knowledge_mcp_ask_consistency.py`
- Verify: `backend/app/tests/test_doc_worker_routing.py`
- Verify: `backend/app/tests/test_doc_support_selection.py`
- Update if implementation details changed: `docs/superpowers/specs/2026-07-17-mcp-ask-request-evidence-top20-design.md`

**Interfaces:**
- Consumes: Completed Tasks 1 and 2.
- Produces: Verified backward-compatible MCP Ask contract and colleague handoff evidence.

- [ ] **Step 1: Run the complete focused regression set**

Run from `backend` with bytecode and pytest cache disabled:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest -p no:cacheprovider `
  tests/test_knowledge_mcp_ask_consistency.py `
  app/tests/test_doc_worker_routing.py `
  app/tests/test_doc_support_selection.py -q
```

Expected: all selected tests pass with zero failures.

- [ ] **Step 2: Verify the public MCP schema**

Start or inspect the MCP server in the existing deployment and confirm `delu_kb_ask` exposes:

```json
{
  "top_k": {
    "type": "integer",
    "default": 20
  }
}
```

Expected: Existing fields remain unchanged and `top_k` is optional.

- [ ] **Step 3: Run real MCP regression**

Call `delu_kb_ask` in one MCP session:

```text
请查询《25年框-浙江欣赞文化创意有限公司.pdf》，汇总其中打印机相关项目、数量、单价和计价方式。
```

Then:

```text
还有其他打印机的历史信息吗？
```

Expected: The first answer's citations/final context can include the target file; the second turn preserves the same session.

- [ ] **Step 4: Verify web isolation**

Run one existing web-chat knowledge question without the MCP runtime parameter.

Expected: Doc Tool uses the existing `synth_text_evidence_cap=5`; no global setting changed.

- [ ] **Step 5: Record final evidence**

Append actual test commands, pass counts, deployment/restart status, and real-query outcomes to the approved design record. Do not claim a Git commit because the mounted directory has no `.git` metadata.

