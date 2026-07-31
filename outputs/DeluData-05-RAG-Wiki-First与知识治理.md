---
title: DeluData RAG Wiki-First 与知识治理
type: technical-notes
tags:
  - 工作/项目
  - 技术/AI
  - 技术/知识库
created: 2026-07-16
updated: 2026-07-16
status: active
---

# DeluData：RAG、Wiki-First 与知识治理

返回：[[DeluData-项目知识索引]]

## 文档入库链路

```text
上传文件
  → 元数据、可见性与 Workspace 校验
  → Ingestion Task 入队
  → 解析/OCR/图片提取
  → 语义分块
  → Embedding 与 BM25 语料构建
  → Chroma/数据库持久化
  → 状态事件通知前端
```

主要入口：`ingestion_service.py`、`core/rag/document_ingestor.py`、`core/rag/document_parser.py`、`entrypoints/ingestion_worker.py`。

## 检索方法

DeluData 使用组合式检索，而不是只做向量相似度：

- Dense Embedding 语义检索。
- BM25 稀疏检索与 Jieba 中文分词。
- 混合召回、结果去重与重排。
- 上下文扩展，补齐相邻块。
- PDF 页码、图片、OCR 和多模态证据。
- `deep_search` 控制更深入的检索策略。

`DocTool` 需要保留 `[[CITATION:...]]` 引用标记；直接绕过标准调用可能导致引用丢失。

## Doc Scope

`services/doc_scope.py` 合并会话级与步骤级文档范围。范围可能按 Workspace、部门、文件或文件夹收窄。Executor 在调用 Doc Worker 前计算最终有效范围，不能只信任前端传入的 ID。

## Wiki-First

Wiki 是从原始文档中编译出的治理知识。Knowledge Router 决定：

- `wiki`：优先查询结构化、已治理知识。
- `rag`：直接查询原始文档块。
- `both`：两条路径合并，用于需要覆盖和证据互补的请求。

决策来源记录为 `disabled`、`rule`、`llm` 或 `fallback`，并写入 Wiki Route Metrics，便于判断 Wiki 命中率、回退率和知识缺口。

## Wiki 编译与治理

- 文档上传或管理员操作产生编译任务。
- 两个 Wiki Compile Worker 使用数据库锁和 Run Token 抢占不同任务。
- 编译过程抽取实体、关系、摘要和导航结构。
- 合并时处理实体去重、孤儿清理和批量归档。
- 配额服务限制 Workspace 的节点、边和编译资源。

## 知识缺口闭环

当 Synthesizer 判断 Wiki 缺少必要知识，会产生 `wiki_gap_signal`。Executor/Service 可将缺口自动入队，供后续编译或管理员治理。完整链路是：

```text
用户问题 → Wiki/RAG 路由 → 检索证据不足
  → Gap Signal → 治理任务 → Wiki 编译
  → 新知识进入下一次检索
```

## Knowledge MCP

Knowledge MCP 让外部 Agent 复用 DeluData 的知识链路，提供：

- `delu_kb_upload_documents`
- `delu_kb_get_tasks`
- `delu_kb_retrieve`

Workspace、部门、可见性和权限由 MCP 服务端环境变量固定，外部 Agent 不能任意选择。远程 HTTP 模式必须配置长随机 Token；本地路径上传默认关闭。

## 修改检查清单

- 是否保留软删除过滤？
- 是否保持 Workspace 和 Doc Scope 隔离？
- 入库状态与前端通知是否一致？
- OCR、图片、页码和引用是否仍可追溯？
- Wiki 路由指标与 Gap Signal 是否落库？
- 队列任务是否支持重试、超时恢复和幂等？
- 检索结果是否能被 Synthesizer 稳定解析？

## 重点测试

- `test_rag_retrieval_regressions.py`
- `test_pdf_extraction.py`
- `test_pdf_multimodal_rag_upgrade.py`
- `test_doc_scope_service.py`
- `test_executor_doc_scope_fallback.py`
- `test_wiki_compiler_entity_merge.py`
- `backend/app/tests/test_wiki_*`
- `test_knowledge_mcp_retrieve_contract.py`

相关：[[DeluData-03-LangGraph-Supervisor执行方法]]、[[DeluData-06-多租户权限与安全模型]]
