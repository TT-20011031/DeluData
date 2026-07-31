# MCP Ask 精确文件证据保底与请求级 Token 预算设计

## 背景

`delu_kb_ask` 已支持请求级 `top_k`，默认最多召回并选择 20 个候选证据。但 `top_k` 只是数量上限，候选随后仍会经过重排、相对分差过滤和 token 预算裁剪。

真实问题：

```text
请查询《25年框-浙江欣赞文化创意有限公司.pdf》，汇总其中打印机相关项目、数量、单价和计价方式。
```

目标文件已存在且状态为 `indexed`。直接检索能够在前 20 个候选中找到目标文件，但目标切片位次靠后，未稳定进入回答智能体的最终证据，最终答案错误地采用了 24 年相似文件。

## 目标

- 用户在 MCP Ask 问题中明确指定知识库文件时，保证该文件至少有可用证据进入最终回答上下文。
- 继续保留普通语义检索到的其他相关文件，不把检索范围限定为目标文件。
- `delu_kb_ask` 公开请求级证据 token 预算参数，允许 MCP 调用方提高本轮证据容量。
- 所有新增行为只对 MCP Ask 生效，知识库网页端保持原行为。

## 非目标

- 不修改 PDF 导入、解析、Markdown 转换、Chroma 或 BM25 索引。
- 不重新导入或重新向量化现有文件。
- 不改变 `delu_kb_retrieve`。
- 不修改网页端默认 `top_k`、4500 token 预算或文件名处理。
- 不保证未明确指定文件名的普通语义问题命中某个特定文件。

## 方案选择

### 采用：目标文件证据保底与普通候选合并

MCP Ask 在请求入口解析明确文件引用，并在当前工作区内按文件元数据查找文件 ID。Doc Worker 继续执行原有普通检索，同时保留目标文件的相关切片。最终证据选择分为两部分：

1. 目标文件保底区：最多 3 个相关切片，最多使用本轮证据 token 预算的一半。
2. 普通相关区：使用剩余切片数和 token 预算，保留普通语义检索结果。

两部分按 `chunk_id` 去重。目标文件切片优先进入最终上下文，但不会删除其他相关文件。

### 不采用：命中文件后限定全部候选

限定文件虽然能保证目标文件出现，但会删除用户可能需要的其他相关历史内容，不符合本次需求。

### 不采用：只提高目标文件分数

单纯加分仍可能被相对分差、token 预算或后续重排淘汰，不能提供确定性保证。

### 不采用：文件名写入向量文本并重新索引

该方案影响全部检索入口，需要重新处理现有文件，实施和回滚成本明显更高。

## MCP 接口

`delu_kb_ask` 增加公开、可选参数：

```python
async def delu_kb_ask(
    query: str,
    reply_model_key: Optional[Literal["flash", "plus", "max"]] = None,
    deep_search: bool = False,
    session_id: Optional[str] = None,
    top_k: int = 20,
    evidence_token_budget: int = 9000,
) -> dict[str, Any]:
    ...
```

`evidence_token_budget` 只控制本次 MCP Ask 送入回答智能体的文本证据预算，不控制模型回答长度。

参数规则：

- 默认值：9000。
- 最小值：2000。
- 最大值：16000。
- 小于 2000 时回退为默认值 9000。
- 大于 16000 时截断为 16000。
- 非整数或无法解析时回退为默认值 9000。
- `top_k` 和 `evidence_token_budget` 分别控制证据条数与证据 token 容量，互不替代。

知识库网页端不经过 MCP Ask 请求入口，不携带该参数，继续使用现有 4500 token 预算。

## 文件名识别

只把用户明确写出的文件引用作为保底目标：

- 书名号：`《文件名.pdf》`
- 明确扩展名：`文件名.pdf`、`.docx`、`.xlsx`、`.pptx`、`.md`、`.txt`

匹配过程：

1. 对引用文件名做 Unicode NFKC、首尾空白和连续空白规范化。
2. 在当前 MCP 绑定工作区中查询未删除文件。
3. 优先完整文件名匹配，不根据公司名或短关键词猜测文件。
4. 一个引用命中一个或多个同名文件时，将可访问的同名文件作为保底目标；后续权限过滤仍由现有检索层执行。
5. 没有完整匹配时不报错、不选择相似文件，继续普通检索。

文件 ID 只在服务内部传递，不返回越权文件信息。

## 请求级数据流

```text
采购项目
  -> delu_kb_ask(
       query,
       session_id,
       top_k=20,
       evidence_token_budget=9000
     )
  -> KnowledgeMcpService.ask
       校验 top_k
       校验 evidence_token_budget 到 2000..16000
       从 query 解析明确文件名
       在 MCP 工作区解析目标 file_ids
  -> ChatService.start_new_session
       注入 mcp_ask_evidence_top_k
       注入 mcp_ask_evidence_token_budget
       注入 mcp_ask_required_file_ids
       注入 mcp_ask_preserve_related_candidates=true
  -> Doc Worker
       普通语义检索
       目标文件精确候选
       合并并按 chunk_id 去重
       目标文件最多 3 块、最多半数 token 预算
       其余预算选择普通相关证据
  -> Synthesizer
  -> MCP answer + citations
```

上述上下文字段只由 `KnowledgeMcpService.ask()` 注入，不写入业务配置，也不永久标记会话类型。每次 MCP Ask 都重新注入本轮参数。

## 组件修改

### `backend/app/mcp/knowledge_server.py`

- 为 `delu_kb_ask` 增加 `evidence_token_budget=9000`。
- 原样传递给 `KnowledgeMcpService.ask()`。

### `backend/app/services/knowledge_mcp_service.py`

- 校验请求级 token 预算。
- 从 MCP query 提取明确文件名。
- 在当前工作区解析匹配的文件 ID。
- 向 ChatService 注入本轮 MCP 保底字段。

### `backend/app/services/chat_service.py`

- 增加显式的 MCP 请求级参数。
- 仅当参数由 MCP 服务传入时写入 `user_context_dict`。
- 网页端调用保持原签名兼容和原行为。

### `backend/app/skills/doc_skill.py`

- 在 MCP 保留相关候选模式下，把目标文件精确候选与全部普通语义候选合并。
- 不再因精确文件命中而过滤掉其他文件。
- 未携带 MCP 标记时保持当前逻辑。

### `backend/app/tools/doc_tool.py`

- MCP Ask 使用请求级 `evidence_token_budget`，网页端继续使用配置值 4500。
- 把目标文件 ID 和“保留普通候选”模式传给 DocSkill。
- 最终证据选择时优先保留目标文件切片，再用剩余预算选择普通候选。
- 在 `meta` 中记录请求预算、目标文件是否进入最终上下文和保底切片数量，便于验收与排障。

### `backend/app/tools/doc_support/selection.py`

- 扩展文本证据选择接口，支持 `required_file_ids`、目标文件最大切片数 3 和目标文件最大预算比例 0.5。
- 保持无目标文件参数时的现有排序与预算行为。

## 错误与降级

- 文件名未匹配：普通检索，不返回“文件不存在”的确定性结论。
- 文件匹配但没有可访问切片：普通检索，并在内部诊断信息中记录 `required_file_evidence_missing`。
- 目标切片超过半数预算：至少保留一个不超过总预算的最高相关切片，其余目标切片不再加入。
- 普通检索失败但目标文件证据可用：使用目标文件证据回答。
- 目标文件检索失败但普通证据可用：使用普通证据，并在内部诊断中记录缺失。
- 所有新增诊断信息不得暴露文件 ID 或越权文件名。

## 测试

1. MCP 工具 schema 公开 `evidence_token_budget`，默认 9000。
2. `KnowledgeMcpService.ask()` 覆盖默认 9000、显式值、下限回退和上限截断。
3. MCP query 能从书名号和明确扩展名提取文件名。
4. 文件名没有完整匹配时不选择相似文件。
5. ChatService 只在 MCP 显式传参时注入请求级字段。
6. 网页端调用继续使用 4500 token 和当前候选逻辑。
7. 目标文件候选位于普通排序第 11 位且分数较低时，仍进入 `final_context`。
8. `final_context` 同时包含目标文件和其他相关文件。
9. 目标文件最多保留 3 个切片，且不超过请求预算的一半。
10. 总文本证据不超过 `top_k` 与请求级 token 预算。
11. `delu_kb_retrieve` 相关测试保持通过。
12. 现有 MCP Ask、网页端 Doc Worker 和证据选择测试保持通过。

## 真实验收

使用同一 MCP 会话测试：

```text
请查询《25年框-浙江欣赞文化创意有限公司.pdf》，汇总其中打印机相关项目、数量、单价和计价方式。
还有其他打印机的历史信息吗？请列出其他文件中的打印机类型、数量、单价和计价方式。
```

验收要求：

- 第一问 `citations/final_context` 必须包含 `25年框-浙江欣赞文化创意有限公司.pdf`。
- 第一问允许同时包含 24 年或其他相关文件，但答案必须优先回答明确指定文件。
- 第二问继续返回其他文件中的历史信息。
- 知识库网页端常规问题的默认预算和行为不变。
- 仅重建并重启 `knowledge-mcp-pro`；如部署结构要求共享后端代码卷，应先确认容器实际加载路径，不重启无关服务。

## 影响

### 会变化

- MCP Ask 默认文本证据预算由知识库全局默认 4500 提高为请求级 9000。
- 明确文件引用会获得最多 3 个目标切片的保底席位。
- MCP Ask 的耗时、模型 token 使用量和费用可能增加。

### 不会变化

- 知识库网页端问答。
- 文件导入、解析和索引。
- `delu_kb_retrieve`。
- 未明确指定文件名的 MCP Ask 检索逻辑，除默认证据 token 预算提高外。
- 权限、工作区、部门和可见性过滤。
