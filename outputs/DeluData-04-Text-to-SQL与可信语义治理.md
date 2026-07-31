---
title: DeluData Text-to-SQL 与可信语义治理
type: technical-notes
tags:
  - 工作/项目
  - 技术/AI
  - 技术/数据库
created: 2026-07-16
updated: 2026-07-16
status: active
---

# DeluData：Text-to-SQL 与可信语义治理

返回：[[DeluData-项目知识索引]]

## Text-to-SQL 链路

```text
业务问题
  → 权限与工作区 readiness
  → SchemaSkill 获取可见 Schema
  → 语义模型解释指标、字段、关系和 Join
  → SQLWorker 生成与修复 SQL
  → SQLSkill 只读校验与执行
  → DataFrame 写入 pending_artifacts
  → Synthesizer 提交到 memory_dfs
```

主要实现位于：

- `backend/app/agents/sql_worker.py`
- `backend/app/skills/schema_skill.py`
- `backend/app/skills/sql_skill.py`
- `backend/app/tools/sql_tool.py`
- `backend/app/core/db/`
- `backend/app/services/db_whitelist_service.py`

## 安全围栏

SQL 执行不是“模型生成后直接运行”，而是依次经过：

1. 用户 Capability 与数据范围。
2. Workspace 数据库白名单。
3. 允许表、字段和语义访问策略。
4. 只读账号与只读执行器。
5. SQL 解析和危险操作拦截。
6. 结果行数、超时和错误类型控制。

任何改动都不能绕过 Workspace、白名单、只读和危险 SQL 拦截。

## 语义层的价值

单纯依赖物理 Schema 会遇到名称歧义、指标口径不统一、Join 猜测和权限难表达。语义层把数据库结构提升为业务可理解资产：

- 表和字段的业务含义。
- 指标定义与聚合规则。
- 实体、关系和 Join 路径。
- 同义词、示例 SQL 和口径说明。
- 行级、对象级和语义访问策略。
- Schema 基线与物理对象状态。

## 运行模式

- `legacy`：兼容旧链路，允许更多回退。
- `shadow`：语义结果用于评估和对比，但不完全接管。
- `trusted`：语义链路为权威链路；语义缺失、澄清需求和 Join 歧义不回退猜测。

切换 Trusted 的硬条件是存在已应用 Schema 基线，并使用只读数据库账号。覆盖率不足会警告，但是否晋级由管理员决定。

## 证据驱动治理闭环

```text
采集证据 → 数据画像 → 生成候选 → 评分去重
  → 冲突检查 → 人工决策/安全自动应用 → 反馈沉淀
```

默认 `observe_only=true`：只画像、评分和生成候选，不修改语义资产。自动应用需要高分、至少两类独立证据、无冲突、非敏感字段并满足白名单条件。低分候选留在证据库，不进入主待办。

`semantic_governance_worker` 通过 Run、Lease、Heartbeat 和 Run Token 执行任务；每个 Workspace 默认限制并发，失联任务会被标记失败并可恢复。

## 问数结果如何继续流转

- SQL 结果先进入 `pending_artifacts`，避免重试时污染长期状态。
- Synthesizer 确认本轮结果后合并进 `memory_dfs`。
- 后续追问、Chart Worker 和 Office Worker可以引用同一 DataFrame。
- Summarizer 根据轮次与大小执行 GC，防止 Checkpoint 无限膨胀。

## 修改方法

1. 先定位是物理 Schema、语义资产、SQL 生成还是执行安全问题。
2. 同时阅读 Service、Model、API、Migration 和测试，不只改 UI 或 Prompt。
3. 用具体 Schema/查询建立最小回归样例。
4. 验证无权限、歧义 Join、危险 SQL、空结果和数据库错误。
5. 检查结果能否被后续图表与 Office 正确消费。

## 重点测试

- `test_sql_worker_trust_modes.py`
- `test_semantic_query_service.py`
- `test_semantic_governance_service.py`
- `test_semantic_evidence_governance.py`
- `test_semantic_access_policy_service.py`
- `test_db_whitelist_service.py`
- `test_database_whitelist_status.py`
- `test_sql_turnover_context.py`

相关：[[DeluData-03-LangGraph-Supervisor执行方法]]、[[DeluData-06-多租户权限与安全模型]]
