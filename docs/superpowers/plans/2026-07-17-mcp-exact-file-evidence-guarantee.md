# MCP Exact File Evidence Guarantee Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 仅为 `delu_kb_ask` 增加请求级证据 token 预算，并保证用户明确指定的文件证据进入最终上下文，同时保留普通语义检索结果。

**Architecture:** MCP Ask 在入口校验预算、解析明确文件名并解析当前工作区文件 ID，通过 ChatService 的显式请求级参数写入本轮 `user_context`。DocSkill 在 MCP 保留模式下合并目标文件精确候选与全部普通候选；证据选择器为目标文件预留最多 3 个切片和最多一半 token 预算，再用剩余预算选择普通证据。没有 MCP 标记时所有现有路径保持原行为。

**Tech Stack:** Python、FastMCP、FastAPI、SQLAlchemy Async、LangGraph、Chroma、pytest/unittest

## Global Constraints

- 只对 MCP Ask 生效；知识库网页端继续使用 4500 token 和原候选逻辑。
- `evidence_token_budget` 默认 9000，最小 2000，最大 16000。
- 目标文件最多保留 3 个切片，最多占本轮证据 token 预算的一半。
- 普通语义检索结果继续保留。
- 不修改导入、解析、向量、BM25、数据库结构和 `delu_kb_retrieve`。
- 挂载副本没有 `.git` 元数据，实施阶段保存文件和测试证据，不创建提交。

---

### Task 1: MCP 参数、文件名解析与请求上下文注入

**Files:**
- Modify: `backend/app/mcp/knowledge_server.py`
- Modify: `backend/app/services/knowledge_mcp_service.py`
- Modify: `backend/app/services/chat_service.py`
- Test: `backend/tests/test_knowledge_mcp_ask_consistency.py`

**Interfaces:**
- Consumes: `delu_kb_ask(query, session_id, top_k, evidence_token_budget)`
- Produces: `ChatService.start_new_session(..., mcp_ask_evidence_token_budget, mcp_ask_required_file_ids, mcp_ask_preserve_related_candidates)`

- [ ] **Step 1: 写入预算和文件名解析失败测试**

在 `KnowledgeMcpAskConsistencyTests` 增加：

```python
async def test_ask_injects_budget_and_explicit_file_targets(self):
    captured = {}

    async def fake_start(_self, **kwargs):
        captured.update(kwargs)
        return {
            "session_id": "session-1",
            "plan_id": "plan-1",
            "steps": [],
            "status": "completed",
            "message": "ok",
        }

    async def fake_plan(_self, _session_id):
        return {"status": "completed", "execution_results": []}

    service = _service()
    service._resolve_required_file_ids = AsyncMock(return_value=["target-file"])
    with (
        patch("app.services.chat_service.ChatService.start_new_session", new=fake_start),
        patch("app.services.chat_service.ChatService.get_session_plan", new=fake_plan),
    ):
        await service.ask(
            query="请查询《25年框-浙江欣赞文化创意有限公司.pdf》的打印机",
        )

    self.assertEqual(captured["mcp_ask_evidence_token_budget"], 9000)
    self.assertEqual(captured["mcp_ask_required_file_ids"], ["target-file"])
    self.assertTrue(captured["mcp_ask_preserve_related_candidates"])
```

增加预算边界测试，依次传入 `6000`、`99999`、`1000`，断言注入值为 `[6000, 16000, 9000]`。

增加纯函数测试，断言能从书名号和 `.pdf` 明确扩展名提取完整文件名，不能从只有公司名称的问题中猜测文件。

- [ ] **Step 2: 运行测试并确认失败原因**

Run:

```bash
cd backend
pytest -q tests/test_knowledge_mcp_ask_consistency.py
```

Expected: FAIL，原因是 `ask()` 尚无 `evidence_token_budget`、文件名解析和 MCP 请求字段。

- [ ] **Step 3: 实施 MCP 服务最小逻辑**

在 `knowledge_server.py` 为 `delu_kb_ask` 增加：

```python
evidence_token_budget: int = 9000,
```

并传给：

```python
payload = await _service().ask(
    query=query,
    reply_model_key=reply_model_key,
    deep_search=deep_search,
    session_id=session_id,
    top_k=top_k,
    evidence_token_budget=evidence_token_budget,
)
```

在 `knowledge_mcp_service.py` 增加常量和纯函数：

```python
MCP_DEFAULT_EVIDENCE_TOKEN_BUDGET = 9000
MCP_MIN_EVIDENCE_TOKEN_BUDGET = 2000
MCP_MAX_EVIDENCE_TOKEN_BUDGET = 16000


def _safe_mcp_evidence_token_budget(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return MCP_DEFAULT_EVIDENCE_TOKEN_BUDGET
    if parsed < MCP_MIN_EVIDENCE_TOKEN_BUDGET:
        return MCP_DEFAULT_EVIDENCE_TOKEN_BUDGET
    return min(parsed, MCP_MAX_EVIDENCE_TOKEN_BUDGET)
```

增加明确文件名提取，使用 Unicode NFKC 和书名号/扩展名模式，不生成公司名猜测：

```python
def _extract_explicit_file_names(query: str) -> list[str]:
    text = unicodedata.normalize("NFKC", str(query or ""))
    matches = re.findall(
        r"《([^》]+?\.(?:pdf|docx?|xlsx?|pptx?|md|txt))》"
        r"|([^\s，。；;：:（）()《》]+?\.(?:pdf|docx?|xlsx?|pptx?|md|txt))",
        text,
        flags=re.IGNORECASE,
    )
    return list(dict.fromkeys(
        candidate.strip()
        for pair in matches
        for candidate in pair
        if candidate and candidate.strip()
    ))
```

增加 `_resolve_required_file_ids()`，只查询当前工作区、未删除且规范化完整文件名相等的 `File.id`。

扩展 `KnowledgeMcpService.ask()`：

```python
evidence_token_budget: int = MCP_DEFAULT_EVIDENCE_TOKEN_BUDGET,
```

并在调用 ChatService 前计算：

```python
safe_budget = _safe_mcp_evidence_token_budget(evidence_token_budget)
explicit_names = _extract_explicit_file_names(query)
required_file_ids = await self._resolve_required_file_ids(
    ctx.workspace_id,
    explicit_names,
)
```

传递：

```python
mcp_ask_evidence_token_budget=safe_budget,
mcp_ask_required_file_ids=required_file_ids,
mcp_ask_preserve_related_candidates=bool(required_file_ids),
```

- [ ] **Step 4: 扩展 ChatService 的显式参数**

给 `start_new_session()` 增加默认均为 `None`/`False` 的参数：

```python
mcp_ask_evidence_token_budget: Optional[int] = None,
mcp_ask_required_file_ids: Optional[list[str]] = None,
mcp_ask_preserve_related_candidates: bool = False,
```

只在 MCP 显式传入时写入：

```python
if mcp_ask_evidence_token_budget is not None:
    user_context_dict["mcp_ask_evidence_token_budget"] = int(
        mcp_ask_evidence_token_budget
    )
if mcp_ask_required_file_ids:
    user_context_dict["mcp_ask_required_file_ids"] = [
        str(file_id) for file_id in mcp_ask_required_file_ids if str(file_id)
    ]
if mcp_ask_preserve_related_candidates:
    user_context_dict["mcp_ask_preserve_related_candidates"] = True
```

- [ ] **Step 5: 运行 Task 1 测试**

Run:

```bash
cd backend
pytest -q tests/test_knowledge_mcp_ask_consistency.py
```

Expected: 全部通过。

---

### Task 2: 目标文件候选与普通候选并存

**Files:**
- Modify: `backend/app/skills/doc_skill.py`
- Test: `backend/tests/test_knowledge_mcp_ask_consistency.py`

**Interfaces:**
- Consumes: `DocSkill.query_knowledge_base(..., preserve_related_candidates: bool = False)`
- Produces: 目标文件精确候选优先、普通语义候选继续保留的去重列表

- [ ] **Step 1: 修改现有测试为两种明确契约**

保留当前默认行为测试：`preserve_related_candidates=False` 时精确文件命中后仍只返回目标文件。

增加 MCP 保留相关候选测试：

```python
chunks = await skill.query_knowledge_base(
    query="查询打印机价格",
    original_query=raw_query,
    user_context=context,
    top_k=5,
    include_images=False,
    preserve_related_candidates=True,
)
self.assertEqual(
    [chunk.chunk_id for chunk in chunks],
    ["target-1", "other-1"],
)
```

- [ ] **Step 2: 运行目标测试并确认失败**

Run:

```bash
cd backend
pytest -q tests/test_knowledge_mcp_ask_consistency.py \
  -k "fuses_exact_filename or preserve_related"
```

Expected: 新测试 FAIL，普通候选 `other-1` 被现有 `exact_file_ids` 过滤。

- [ ] **Step 3: 增加请求级合并开关**

扩展签名：

```python
preserve_related_candidates: bool = False,
```

把当前精确命中后的过滤条件改为：

```python
semantic_candidates = reranked
if exact_file_ids and not preserve_related_candidates:
    semantic_candidates = [
        chunk
        for chunk in reranked
        if str((getattr(chunk, "metadata", {}) or {}).get("file_id") or "")
        in exact_file_ids
    ]
```

保留现有精确候选优先、`chunk_id` 去重和 `top_k` 截断。

- [ ] **Step 4: 运行 DocSkill 测试**

Run:

```bash
cd backend
pytest -q tests/test_knowledge_mcp_ask_consistency.py
```

Expected: 默认限定测试和 MCP 并存测试均通过。

---

### Task 3: 目标文件最终证据保底与请求预算

**Files:**
- Modify: `backend/app/tools/doc_support/selection.py`
- Modify: `backend/app/tools/doc_tool.py`
- Test: `backend/app/tests/test_doc_support_selection.py`
- Test: `backend/app/tests/test_doc_worker_routing.py`

**Interfaces:**
- Consumes: `required_file_ids`, `required_max_chunks=3`、`required_budget_ratio=0.5`
- Produces: 同时满足目标文件保底、普通候选保留、`max_chunks` 和总 token 预算的 `selected_chunks`

- [ ] **Step 1: 写入选择器失败测试**

构造 10 个高分普通切片和 2 个低于相对分差的目标切片，调用：

```python
selected, used_tokens, trimmed = selection.select_chunks_for_synthesizer(
    chunks=chunks,
    token_budget=9000,
    score_threshold=0.12,
    relative_margin=0.08,
    max_chunks=20,
    required_file_ids={"target"},
    required_max_chunks=3,
    required_budget_ratio=0.5,
)
```

断言：

```python
assert any(chunk.metadata["file_id"] == "target" for chunk in selected)
assert any(chunk.metadata["file_id"] != "target" for chunk in selected)
assert sum(chunk.metadata["file_id"] == "target" for chunk in selected) <= 3
assert used_tokens <= 9000
```

- [ ] **Step 2: 运行选择器测试并确认失败**

Run:

```bash
cd backend
pytest -q app/tests/test_doc_support_selection.py \
  -k "required_file"
```

Expected: FAIL，现有函数不接受保底参数或低分目标切片被相对分差删除。

- [ ] **Step 3: 实施两阶段证据选择**

扩展 `select_chunks_for_synthesizer()` 的可选参数：

```python
required_file_ids: set[str] | None = None,
required_max_chunks: int = 3,
required_budget_ratio: float = 0.5,
```

算法：

1. 按分数排序全部有效切片。
2. 从 `required_file_ids` 中先选最多 3 个，目标累计 token 不超过 `token_budget * 0.5`；若第一个目标切片超过半数预算但不超过总预算，仍保留第一个。
3. 遍历普通排序，跳过已选 `chunk_id`，继续应用现有绝对阈值和相对分差。
4. 普通候选使用剩余 `max_chunks` 和剩余总 token 预算。
5. 没有 `required_file_ids` 时执行现有逻辑，保持结果顺序和日志契约。

- [ ] **Step 4: 写入 Doc Worker 请求级预算测试**

在 `test_doc_worker_routing.py` 增加测试，上下文包含：

```python
{
    "mcp_ask_evidence_top_k": 20,
    "mcp_ask_evidence_token_budget": 9000,
    "mcp_ask_required_file_ids": ["target-file"],
    "mcp_ask_preserve_related_candidates": True,
}
```

断言：

- `query_knowledge_base()` 收到 `preserve_related_candidates=True`。
- `final_context` 同时包含 `target-file` 和普通文件。
- `result.meta["evidence_token_budget"] == 9000`。
- `result.meta["required_file_evidence_count"] >= 1`。

再保留无标记请求测试，断言仍使用默认最终证据上限 5，且不注入保底参数。

- [ ] **Step 5: 修改 Doc Worker**

解析并限制 MCP 请求级预算：

```python
raw_budget = raw_user_context.get("mcp_ask_evidence_token_budget")
if raw_budget is None:
    token_budget = settings.supervisor.rag_token_budget_per_request
else:
    try:
        parsed_budget = int(raw_budget)
    except (TypeError, ValueError):
        parsed_budget = 9000
    token_budget = 9000 if parsed_budget < 2000 else min(parsed_budget, 16000)
```

解析目标文件 ID：

```python
required_file_ids = {
    str(file_id)
    for file_id in raw_user_context.get("mcp_ask_required_file_ids") or []
    if str(file_id)
}
preserve_related_candidates = bool(
    raw_user_context.get("mcp_ask_preserve_related_candidates")
    and required_file_ids
)
```

传给 DocSkill：

```python
preserve_related_candidates=preserve_related_candidates,
```

传给选择器：

```python
required_file_ids=required_file_ids,
required_max_chunks=3,
required_budget_ratio=0.5,
```

在 `meta` 增加：

```python
"evidence_token_budget": token_budget,
"required_file_evidence_count": sum(
    1 for item in final_context
    if str(item.get("file_id") or "") in required_file_ids
),
"required_file_evidence_missing": bool(
    required_file_ids
    and not any(
        str(item.get("file_id") or "") in required_file_ids
        for item in final_context
    )
),
```

- [ ] **Step 6: 运行 Task 3 测试**

Run:

```bash
cd backend
pytest -q app/tests/test_doc_support_selection.py app/tests/test_doc_worker_routing.py
```

Expected: 全部通过。

---

### Task 4: 全量回归、部署、真实验收和同事文档

**Files:**
- Modify: `docs/superpowers/specs/2026-07-17-mcp-exact-file-evidence-guarantee-design.md`
- Create: `outputs/DeluData-MCP精确文件证据保底实施记录.md`

**Interfaces:**
- Consumes: 完成的 MCP Ask 修复和线上 `knowledge-mcp-pro`
- Produces: 自动化测试证据、真实问答证据和可交接实施记录

- [ ] **Step 1: 运行相关完整测试**

Run:

```bash
cd backend
pytest -q \
  tests/test_knowledge_mcp_ask_consistency.py \
  app/tests/test_doc_worker_routing.py \
  app/tests/test_doc_support_selection.py
```

Expected: 全部通过。

- [ ] **Step 2: 确认修改隔离**

检查：

```bash
rg -n "mcp_ask_evidence_token_budget|mcp_ask_required_file_ids|mcp_ask_preserve_related_candidates" \
  app tests
```

Expected: 请求标记只从 `KnowledgeMcpService.ask()` 注入；网页端入口没有设置这些字段。

- [ ] **Step 3: 部署最小服务范围**

在服务器项目中确认 compose 服务和代码加载路径，仅重新构建并启动 `knowledge-mcp-pro`：

```bash
docker compose up -d --build knowledge-mcp-pro
docker compose ps knowledge-mcp-pro
docker compose logs --tail=100 knowledge-mcp-pro
```

Expected: 容器 healthy/Up，工具 schema 出现 `evidence_token_budget` 默认 9000。

- [ ] **Step 4: 真实 MCP 回归**

在同一 MCP 会话依次询问：

```text
请查询《25年框-浙江欣赞文化创意有限公司.pdf》，汇总其中打印机相关项目、数量、单价和计价方式。
还有其他打印机的历史信息吗？请列出其他文件中的打印机类型、数量、单价和计价方式。
```

Expected:

- 第一问 citations 包含 `25年框-浙江欣赞文化创意有限公司.pdf`。
- 第一问可以同时包含其他相关文件。
- 第二问返回其他文件历史信息。

- [ ] **Step 5: 验证网页端隔离**

用知识库网页端常规问题执行一次问答，并从诊断/日志确认没有 `mcp_ask_*` 请求标记，证据预算仍为 4500。

- [ ] **Step 6: 写入同事交接记录**

文档必须包含：

- 业务症状和根因。
- 代码修改文件及数据流。
- MCP 参数 schema、默认值和边界。
- 自动化测试数量与结果。
- 真实问题和 citations 结果。
- 部署范围。
- 网页端不受影响的验证证据。
- 回滚方式：回退五个代码文件并仅重建 `knowledge-mcp-pro`。
