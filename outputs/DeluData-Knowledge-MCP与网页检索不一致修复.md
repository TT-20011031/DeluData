---
title: DeluData Knowledge MCP 与网页检索不一致修复
type: troubleshooting
tags:
  - 工作/项目
  - 技术/AI
  - 技术/API
created: 2026-07-16
updated: 2026-07-16
status: solved
related:
  - "[[DeluData-项目知识索引]]"
---

# DeluData Knowledge MCP 与网页检索不一致修复

## 症状

网页端询问“杭州泰泽办公设备有限公司提供的打印机历史价格是多少”能够命中 `24年-1-杭州泰泽办公设备有限公司.pdf`，但 MCP 的 `delu_kb_ask` 会随机引用其他供应商，且多次结果不稳定。

正确数据：大型打印机 3700 元/台，小型桌面打印机 290 元/台。

## 根因

1. `KnowledgeMcpService.ask()` 固定使用 `execution_mode="rag_only"`，没有复用网页的 Auto Supervisor 工作流。
2. `DocSkill` 的文件名/完整术语精确检索只在语义检索零候选或重排后为空时执行。宽泛词“打印机”先召回其他供应商后，杭州泰泽文件无法进入候选。
3. Auto Executor 没有把当前用户原始问题作为 `original_query` 传给 Doc Worker，查询改写可能弱化完整公司名。
4. MCP ask 只返回自然语言答案和计划状态，没有返回 Synthesizer 实际使用的文件引用。

## 修复

- `delu_kb_ask` 改用 `auto`，并同步确认计划、等待执行完成。
- 每次 RAG 都执行文件名/完整术语精确匹配，并与语义候选融合。
- 完整公司名命中文件名后，将本轮候选限定到命中文件，防止混入其他供应商。
- 即使 Embedding/语义检索暂时失败，也保留精确文件命中作为降级证据。
- Executor 和 Doc Tool 透传当前原始问题。
- MCP 返回 `citations`，内容来自执行结果的 `final_context`。
- `ChatService.confirm_and_execute()` 返回最终状态，供同步 MCP 调用复用。

## 回归与线上验收

- 5 个异步回归测试通过：Auto 编排、引用返回、精确候选融合、文件限定、Embedding 失败降级、原问透传。
- 真实 Chroma/MySQL 单问与复合问均能直接命中目标 PDF 和 3700/290。
- 重建并重启 `knowledge-mcp-pro`，未重启 Admin、Kiosk、MySQL、Chroma 或后台 Worker。
- 价格问题连续调用 10 次，10/10 通过：每次只引用杭州泰泽 PDF，均返回 3700 和 290，无浙江欣赞、盛郑艺唐引用。
- 复合问题通过：资质部分说明当前缺少营业执照/信用评级证据；价格子查询仍只引用杭州泰泽文件。

## 关键经验

- MCP 的“完整问答”接口不应复制一套简化 RAG，应复用与网页一致的业务编排。
- 实体名/文件名精确召回应作为稳定候选源，而不是零召回兜底。
- 精确命中文件后仅提高排序仍可能产生错误次要引用；验收要求严格时应限定候选文件。
- 最终回答接口应返回实际使用的引用信息，便于调用方验证答案来源。
- 非确定性的 LLM/RAG 缺陷必须通过多次重复验收，而不是单次成功判断修复完成。
