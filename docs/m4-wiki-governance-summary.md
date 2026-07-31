# M4 Wiki 治理 — 实施摘要

> 立项目标：在 M3 Wiki-First 路由完成的基础上，补齐"治理回路"——
> 让 Wiki Gap 信号自动触发后台编译，让 WikiCompiler 在运行时受配额约束，
> 并通过 API + 前端可视化暴露配额观测，避免"知识盲区永远是盲区"和"配额超用打爆数据库"两类风险。
>
> 本期范围：**M4.1**（Wiki Gap 自动入队）、**M4.2**（配额运行时校验 + API + 前端）。
> 已显式延后：**M4.3**（Lint 报告 UI）、**M4.4**（修订审计 UI），交后续迭代实现。

---

## 1. 功能矩阵

| 里程碑 | 内容 | 关键文件 | 测试 |
|--------|------|----------|------|
| **M4.1** | Executor finalize 抽取 `wiki_gap_signal` → `WikiCompileQueueService.enqueue` 自动入队（fire-and-forget + 去重） | `app/supervisor/nodes/executor.py` | `test_wiki_gap_autoenqueue.py`（8 用例） |
| **M4.2 后端** | `WikiCompiler._apply_outcome` 配额运行时校验（`max_pages_per_workspace` / `max_links_per_page` 截断） + `WikiService.get_quota_status` + `GET /api/wiki/quota` | `app/services/wiki_service.py`、`app/api/wiki/{router,schemas}.py` | `test_wiki_quota.py`（11 用例） |
| **M4.2 前端** | `wikiService.getQuota` + `WikiRouteHealthCard` 增加配额条小节（实体页 + 单页出链 + near_limit 告警） | `frontend/src/services/wikiService.ts`、`frontend/src/components/wiki/WikiRouteHealthCard.tsx` | 借由 `WikiRouteHealthCard` 复用 M3 收尾的渲染路径 |

---

## 2. M4.1：Wiki Gap 自动入队

### 2.1 触发链路

```
worker 产物 → Executor.finalize()
    ├── _record_wiki_route_metrics(...)        # M3.5 埋点
    └── _enqueue_wiki_gap_task(...)            # M4.1 新增
            ↓
        WikiCompileQueueService.enqueue(
            workspace_id, file_ids, source="wiki_gap_auto", ...
        )
            ↓
        反思编译队列 → 后台 worker 拉起 WikiCompiler
```

### 2.2 设计要点

- **Fire-and-forget**：`_enqueue_wiki_gap_task` 不抛出，所有异常本地捕获并写日志，避免污染主问答链。
- **去重**：交由 `WikiCompileQueueService.enqueue` 的内置 `(workspace_id, file_ids_hash)` 唯一键负责。
- **Workspace 守卫**：缺失 `workspace_id` 时直接 short-circuit，避免错入队到默认空间。
- **user_query 截断**：写入 `enqueue.user_query` 字段时硬限 256 字符，防止过长 query 把日志撑爆。
- **空信号短路**：当 `wiki_gap_signal.file_ids` 为空时立即返回，不产生空任务。

### 2.3 单测覆盖（`test_wiki_gap_autoenqueue.py`）

| 用例 | 断言要点 |
|------|----------|
| 正常入队 | service.enqueue 被调用一次，参数透传正确 |
| 空 file_ids 短路 | service.enqueue 未被调用 |
| 缺失 workspace_id 短路 | service.enqueue 未被调用 |
| service 抛异常被吞 | finalize 主链不抛错，记录 logger.warning |
| user_query 超长截断 | enqueue 入参 user_query 长度 ≤ 256 |
| compile_task_id 回写 | 入队成功时把 task_id 写到 metrics 透传 |
| 去重命中 | service.enqueue 返回 None 时 finalize 仍正常 |
| 已禁用 wiki 时不入队 | wiki.first_enabled=false 时 short-circuit |

---

## 3. M4.2：配额运行时校验

### 3.1 双层防御

```
┌────────────────────────────────────────────────────────────┐
│ Layer 1：编译时（_apply_outcome 内）                        │
│  - 拉取当前已落库 published/draft 页数 → pages_remaining   │
│  - 对每个 will_create 候选检查 pages_remaining，触达截断    │
│  - 单页 outgoing_links > max_links → 截断到 max_links      │
│  - 跳过的写入 outcome.error_messages，统计 quota_breach     │
└────────────────────────────────────────────────────────────┘
            ↑
┌────────────────────────────────────────────────────────────┐
│ Layer 2：观测态（get_quota_status / GET /api/wiki/quota）   │
│  - count(pages where status != archived)                   │
│  - max(count(*)) per source_page_id on wiki_links          │
│  - usage_pct >= 0.9 → near_limit=true（前端告警）          │
└────────────────────────────────────────────────────────────┘
```

### 3.2 配额配置（`settings.wiki`）

| 字段 | 默认值 | 含义 |
|------|--------|------|
| `max_pages_per_workspace` | 500 | 工作区允许的最大实体页数（archived 不计） |
| `max_links_per_page` | 50 | 单页允许的最大 outgoing_links 条数 |

两者均通过 `_apply_outcome` 拿到，环境变量可覆盖。

### 3.3 API：`GET /api/wiki/quota`

返回 `WikiQuotaStatus`：

```jsonc
{
  "workspace_id": "ws-xxx",
  "max_pages_per_workspace": 500,
  "max_links_per_page": 50,
  "pages": { "current": 123, "limit": 500, "usage_pct": 0.246 },
  "links": { "max_per_page": 50, "max_outgoing_observed": 38 },
  "near_limit": false
}
```

权限：登录用户即可（与 `/api/wiki/metrics/route` 一致）。

### 3.4 前端：健康度卡片配额条

`WikiRouteHealthCard` 在路由健康度块下方追加 `QuotaSection`：

- 双进度条：实体页（current/limit）、单页最大出链（observed/max）。
- 颜色阶梯：`< 70%` 绿、`70%–90%` 琥珀、`>= 90%` 玫红。
- `near_limit=true` 时右上角显示 "接近上限" pill。
- 配额请求与路由健康度并行 fetch，`getQuota` 失败时不阻塞主指标渲染。

### 3.5 单测覆盖（`test_wiki_quota.py`，11 用例 / 0 失败）

`TestApplyOutcomeQuota`（_apply_outcome 配额边界）：

- 未达上限 → 全部正常落库
- 触达上限 → 新建被截断至剩余额度，超量写入 `error_messages`
- 已超上限 → 全部 create 被截断
- update 不占配额（已存在 slug 走 update 路径）
- 单页 outgoing_links 超限被截断（`links_dropped` 计数正确）
- 混合 create/update 部分截断（验证 `pages_remaining` 同批递减 + `existing_slug_set` 同步更新）

`TestGetQuotaStatus`（get_quota_status 三态）：

- 空工作区 → 全 0、`near_limit=false`
- 正常使用 → `usage_pct` 计算正确
- pages 接近上限 → `near_limit=true`
- links 接近上限 → `near_limit=true`

`TestQuotaRouteIntegration`：

- 路由 endpoint `get_wiki_quota_status` 调用 `WikiService.get_quota_status` 并透传结果

---

## 4. 测试与回归

| 测试集 | 用例数 | 结果 |
|--------|--------|------|
| Wiki/知识路由完整集（test_wiki_*.py + test_knowledge_router.py + test_doc_support_selection.py + test_followup_intent.py） | **118** | **全部通过** |
| 全量后端 pytest（排除 pre-existing 失效） | 148 通过 / 11 失败 / 1 ERROR | 11 失败均为 **历史预存** 问题（无 MySQL 服务、`BlankFieldDetector` 重构残留、`test_agent_workflow` 引用已删模块），与 M4 完全无关 |

回归运行命令：

```bash
cd backend
/root/.venv/bin/pytest \
    app/tests/test_wiki_quota.py \
    app/tests/test_wiki_gap_autoenqueue.py \
    app/tests/test_wiki_metrics.py \
    app/tests/test_wiki_scope_filter.py \
    app/tests/test_wiki_e2e_pipeline.py \
    app/tests/test_wiki_navigator.py \
    app/tests/test_wiki_gap_and_citations.py \
    app/tests/test_knowledge_router.py \
    app/tests/test_doc_support_selection.py \
    app/tests/test_followup_intent.py \
    -p no:cacheprovider
# => 118 passed
```

### 4.1 测试基础设施补齐

本期顺手补齐了 backend 测试运行环境：

- 新增 `backend/conftest.py`：把 `backend/` 显式加入 `sys.path`，让所有测试顶部的 `from app.xxx import ...` 在 collection 阶段就能解析（不再依赖 pytest rootdir 推断）。
- 修复 `app/models/knowledge/legacy.py` 的循环 import：`DocumentStatus` 改从 `app.models.common.enums` 直取，避开 `app.api.knowledge.__init__` → `documents.py` → `auth/router.py` → `deps.py` 的死循环（这是预存问题，本期触发后顺手修掉）。

---

## 5. 已显式延后

- **M4.3**：Lint 报告 UI（基于 `/api/wiki/lint` 已有的后端报告做可视化）
- **M4.4**：修订审计 UI（基于 `wiki_revisions` 表做时间线 + diff 展示）

两者均不涉及核心路由/编译稳定性，留给后续在 UI 节奏更宽松时继续推进。

---

## 6. 关键文件索引

```
backend/
├── app/supervisor/nodes/executor.py              # M4.1 _enqueue_wiki_gap_task
├── app/services/wiki_service.py                  # M4.2 _apply_outcome 配额校验 + get_quota_status
├── app/api/wiki/router.py                        # M4.2 GET /quota
├── app/api/wiki/schemas.py                       # M4.2 WikiQuotaStatus 系列 Schema
├── app/models/knowledge/legacy.py                # 修循环 import（DocumentStatus 直取 enums）
├── conftest.py                                   # 测试 sys.path 修复
└── app/tests/
    ├── test_wiki_gap_autoenqueue.py              # M4.1 单测
    └── test_wiki_quota.py                        # M4.2 单测

frontend/
└── src/
    ├── services/wikiService.ts                   # M4.2 getQuota + WikiQuotaStatus 类型
    └── components/wiki/WikiRouteHealthCard.tsx   # M4.2 QuotaSection / QuotaBar
```
