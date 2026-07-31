# MCP Ask 请求级证据上限 20 隔离设计

## 背景

采购审查项目通过 `delu_kb_ask` 获取知识库智能体生成的自然语言答案。真实问题中，目标文件的相关切片已被知识库召回，但位于第 11 位；Doc Worker 最终只保留前 5 个文本证据，导致回答智能体看不到目标文件。

本次只扩大 MCP Ask 请求的检索与最终证据数量，不修改 PDF 导入、解析、向量、文件名匹配、排序算法或知识库网页端行为。

## 目标

- 所有通过 `delu_kb_ask` 发起的请求，默认最多召回并向回答智能体提供 20 个文本证据。
- 知识库网页端继续使用现有默认值，不受本次调整影响。
- 不要求采购审查项目修改现有 MCP 调用参数。
- 不修改现有知识库数据，不需要重新导入或重新索引文件。

## 非目标

- 不修改 `delu_kb_retrieve`。
- 不修改文件名精确匹配或重排逻辑。
- 不把 PDF 转换为 Markdown。
- 不永久修改或标记知识库会话。
- 不保证回答一定使用全部 20 个证据；token 预算仍可继续裁剪上下文。

## 方案选择

### 采用：MCP Ask 公开请求级 `top_k`

`delu_kb_ask` 增加一个公开、可选的请求参数：

```python
async def delu_kb_ask(
    query: str,
    reply_model_key: Optional[Literal["flash", "plus", "max"]] = None,
    deep_search: bool = False,
    session_id: Optional[str] = None,
    top_k: int = 20,
) -> dict[str, Any]:
    ...
```

`top_k` 的语义统一为“本次 MCP Ask 的检索数量和最终文本证据上限”，服务端限制在 `1..20`。默认 20，因此现有客户端不传该参数时也自动使用 20；其他 MCP 客户端可按单次请求覆盖。

`KnowledgeMcpService.ask()` 对 `top_k` 做服务端校验后，在调用 `ChatService.start_new_session()` 时注入仅本次运行有效的上下文字段：

```text
mcp_ask_evidence_top_k = safe_top_k
```

该字段由 MCP Ask 入口生成，同时表明请求来自 MCP Ask，并携带本次证据数量上限。每次 MCP Ask 调用都重新注入，不写入独立业务表，也不把会话永久标记为 MCP 会话。

Doc Worker 读取该字段后：

1. 将本次 RAG 检索 `top_k` 至少提高到 20；
2. 将本次 Synthesizer 文本证据上限提高到 20；
3. 继续服从现有 token 预算、权限、部门、可见性和安全范围过滤。

知识库网页端直接调用 `ChatService`，不会经过 `KnowledgeMcpService.ask()`，因此没有该字段，继续使用现有默认检索和 5 条最终文本证据。

### 不采用：全局配置改为 20

直接修改 `RAG_SYNTH_TEXT_EVIDENCE_CAP` 会同时影响网页端和其他知识库调用方，不符合隔离要求。

### 不采用：永久会话类型标记

把 `session_id` 持久化标记为 MCP 会话会增加状态管理和跨入口兼容复杂度。请求入口本身已经能够确定调用来自 MCP，无需增加永久状态。

## 参数

`delu_kb_ask.top_k` 默认值为 20，服务端限制在 `1..20`。请求显式传入时使用请求值；未传时直接使用工具默认值 20。

本次不增加全局环境变量或工作区配置，避免扩大配置面。该参数是可选参数，现有采购项目和其他客户端不需要同步升级。

## 数据流

```text
采购审查项目
  -> delu_kb_ask(query, session_id, top_k=20 可省略)
  -> KnowledgeMcpService.ask
       校验 top_k 到 1..20
       注入 mcp_ask_evidence_top_k=safe_top_k
  -> ChatService.start_new_session(extra_context=...)
  -> Supervisor / Executor
  -> DocTool
       retrieval top_k = 20
       final text evidence cap = 20
  -> 知识库 Synthesizer
  -> MCP 返回自然语言 answer + citations
```

## 影响范围

### 会变化

- 所有 `delu_kb_ask` 调用最多可向知识库回答智能体提供 20 个文本证据。
- MCP Ask 的 token 使用量、回答耗时和模型费用可能增加。
- 更多相似文件可能进入上下文，回答信息更完整，也可能增加一定噪声。

### 不会变化

- 知识库网页端问答默认行为。
- 原始 PDF、MySQL 文件记录、Chroma 向量和 BM25 索引。
- `delu_kb_retrieve` 返回行为。
- 当前采购审查项目的既有 MCP 请求仍然兼容；新增 `top_k` 为可选参数。
- 会话绑定与历史上下文机制。

## 边界与降级

- 请求值小于 1 时回退为默认值 20，大于 20 时截断为 20。
- 即使证据上限为 20，现有 token 预算仍有权减少实际进入 Synthesizer 的切片数。
- 未携带 `mcp_ask_evidence_top_k` 的调用保持原有行为。
- MCP 工具未传 `top_k` 时采用默认值 20，不改变网页端全局配置。

## 测试要求

1. `delu_kb_ask` 必须公开可选参数 `top_k`，默认 20，并保持旧请求兼容。
2. `KnowledgeMcpService.ask()` 必须校验 `top_k` 并向 `ChatService` 注入请求级 `mcp_ask_evidence_top_k=safe_top_k`。
3. Doc Tool 收到该标记时，检索 `top_k` 和最终文本证据上限均使用相同的安全值。
4. 显式传入 `top_k=8` 时，本次检索和最终文本证据上限均使用 8。
5. 传入大于 20 的值时截断为 20；非法或小于 1 的值回退为默认 20。
6. Doc Tool 未收到该标记时，仍使用现有默认值和 `synth_text_evidence_cap=5`。
7. `delu_kb_ask` 的现有返回结构保持兼容。
8. `delu_kb_retrieve` 相关测试保持通过。
9. 使用真实问题回归时，检查目标 `25年框-浙江欣赞文化创意有限公司.pdf` 是否进入 MCP Ask 的 `citations/final_context`。

## 验收问题

同一 MCP 会话依次测试：

1. `请查询《25年框-浙江欣赞文化创意有限公司.pdf》，汇总其中打印机相关项目、数量、单价和计价方式。`
2. `还有其他打印机的历史信息吗？`

同时在知识库网页端重复测试一个常规问题，确认网页端的检索数量和回答行为未因 MCP 请求级标记而改变。

## 实施与验证结果（2026-07-17）

已按本设计实施，修改范围仅限 MCP Ask 请求链：

- `backend/app/mcp/knowledge_server.py`：`delu_kb_ask` 新增可选参数 `top_k`，默认 20。
- `backend/app/services/knowledge_mcp_service.py`：对请求值执行默认、回退和 `1..20` 限制。
- `backend/app/services/chat_service.py`：仅通过显式服务端参数注入 `mcp_ask_evidence_top_k`。
- `backend/app/tools/doc_tool.py`：仅在存在上述 MCP Ask 标记时，同时调整检索数量和最终文本证据上限。
- 未修改网页端入口、全局 RAG 配置、文件解析、向量索引、数据库结构及 `delu_kb_retrieve`。

自动化验证结果：

- `tests/test_knowledge_mcp_ask_consistency.py`
- `app/tests/test_doc_worker_routing.py`
- `app/tests/test_doc_support_selection.py`
- 合计：33 passed，0 failed。
- 已覆盖默认 20、显式 8、超过上限截断为 20、小于 1 回退为 20，以及无 MCP 标记时仍保留网页端最终证据上限 5。

部署验证结果：

- 仅重新构建并启动 `knowledge-mcp-pro` 容器，知识库网页后端、导入 Worker、MySQL、Redis 等容器未重建。
- MCP 在线工具 schema 已确认 `top_k` 为可选整数参数，默认值 20。
- 采购项目当前客户端未显式传 `top_k`，已通过 MCP 工具默认值 20 生效，旧调用方式保持兼容。

真实同会话验证：

1. 第一问成功，知识库返回会话 `4defd91d-4b90-4d05-8a56-30c9b6195b4d`。
2. 使用同一会话追问“还有其他打印机的历史信息吗？”成功。
3. 第二问补充引用了 `24年-3-浙江欣赞文化创意有限公司.pdf` 和 `24年-4-浙江欣赞文化创意有限公司.pdf`，不再只停留在第一问指定的 25 年文件。

说明：挂载目录当前未包含 `.git` 元数据，因此本次无法在该挂载副本中创建提交；修改文件和验证记录均保留在服务器项目目录中，后续请由知识库项目负责人纳入其正式版本管理。
