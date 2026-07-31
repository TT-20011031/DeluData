---
domain: product
title: DeluData 架构与组件
version: v3.0
---

# DeluData 架构与组件

## 定义

DeluData 采用 **Supervisor-Worker** 多智能体架构，由 [[supervisor]] 负责理解用户意图、拆解任务，并把每个子任务分派给对应的 [[worker]] 执行；执行结果再由 Supervisor 合成最终答案。所有跨工作区的检索请求统一走 [[knowledge-router]] 做路径判定。

## 核心组件

### Supervisor（编排层）

**Supervisor** 是整条链路的"调度员"。它接到一条用户消息后，会依次完成：

- 意图分类（问数 / 问知识 / 闲聊 / 文件处理）
- 任务拆解（一个用户问题可能被拆为多个 Worker 子任务）
- 路径调度（涉及知识检索时，把请求转给 [[knowledge-router]]）
- 答案合成（把多个 Worker 的执行结果整合成最终回答 + citation）

Supervisor 不直接调用工具，它只决定"谁来做"。

### Worker（执行层）

**Worker** 是按领域划分的执行单元，每个 Worker 只关心自己的一类任务：

- `doc_worker` — 知识检索（RAG / Wiki / 两者合并）
- `sql_worker` — 自然语言转 SQL，执行查询并产出结果集
- `office_worker` — Word / Excel 文档生成与精准修改
- `chart_worker` — 数据可视化生成（HTML 图表）

每个 Worker 都通过 [[skill]] 暴露能力，可独立替换或扩展。

### Skill（能力层）

**Skill** 是 Worker 内部的一组可复用工具。例如 `doc_worker` 内部包含：

- `query_knowledge_base` — 切片检索 Skill（走 [[rag]]）
- `wiki_navigate` — 实体页定向装载 Skill（走 [[wiki]]）

Skill 通过统一的工具协议被 Worker 调用，便于在不改业务代码的前提下扩展能力。

### Knowledge Router（路由层）

[[knowledge-router]] 是 DeluData 的"流量分发器"，位于 Supervisor 调度 `doc_worker` 之前。它的输入是用户 query 与可选的 Scope，输出是 `wiki / rag / both` 三选一。

它使三类问题分别走最合适的路径：

- "是什么 / 对比" → 走 [[wiki]] 实体页
- "原文摘录 / 第几页" → 走 [[rag]] 切片
- 模糊或长复合问 → 走 both（两路并行 + 合并去重）

## 三层与外部依赖的关系

DeluData 与外部系统形成三层依赖：

- **L1 展现层** — React + Shadcn/UI，通过 SSE 双轨事件流接收"思维链"和"对话"两路输出。
- **L2 编排层** — 上述 Supervisor / Worker / Skill / Router。
- **L3 资源层** — MySQL（业务表、Wiki 实体页表）+ ChromaDB（向量索引）+ OSS（原始文档存储）+ LLM Provider（百炼 / 千问 / OpenAI 等）。

工作区（Workspace）是 DeluData 的隔离单位：所有 Wiki 实体页、文档、向量索引、用户、权限都按 workspace_id 严格隔离。

## 常见问题

**Q：Supervisor 与 Worker 的边界在哪里？**
A：Supervisor 决定"做什么 / 谁来做"，Worker 决定"怎么做 / 调哪个工具"。两者通过结构化消息通信，不共享内部状态。

**Q：新增一个领域能力（比如"翻译 Worker"）需要改哪里？**
A：实现一个新的 Worker 子类 + 注册若干 [[skill]]，Supervisor 端只需要加一个意图分类标签即可路由过来，不需要改其他 Worker。

**Q：[[knowledge-router]] 失败了会怎样？**
A：路由失败会回退到默认路径（默认 `both`，即同时走 [[rag]] 与 [[wiki]] 并合并），保证用户问题永远有答。
