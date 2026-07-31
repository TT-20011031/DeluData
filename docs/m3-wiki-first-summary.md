# M3 Wiki-First 知识路由 — 实施摘要

> 立项目标：在不破坏现有 RAG 链路的前提下，引入"Karpathy 模式"的 Wiki 实体页层，
> 通过 KnowledgeRouter 在 query → retrieve 之前判定 `wiki / rag / both`，
> 让"是什么"类的概念性问题直接命中预编译实体页，并保留 RAG 的"原文摘录"能力。

本次落地的 M3 共 6 个里程碑（M3.1 → M3.5 + 收尾），全部以 KISS / 渐进式 / 零回归为准则推进。

---

## 1. 功能矩阵

| 里程碑 | 内容 | 关键文件 | 测试 |
|--------|------|----------|------|
| **M3.1** | KnowledgeRouter 节点 + 规则/LLM/Fallback 三段式路由 | `app/supervisor/nodes/knowledge_router.py` | `test_knowledge_router.py` |
| **M3.2** | WikiNavigator：INDEX → LLM 选页 → 整页装载 | `app/core/wiki/navigator.py` | `test_wiki_navigator.py` |
| **M3.3** | doc_worker 双路径（rag / wiki / both）+ 合并去重 | `app/tools/doc_tool.py`、`app/skills/doc_skill.py` | `test_doc_worker_routing.py` |
| **M3.4** | Synthesizer Wiki Gap 检测 + Citation 透传 | `app/supervisor/nodes/synthesizer.py` | `test_wiki_gap_and_citations.py` |
| **M3.5** | 路由评估埋点 + 三级范围选择（域 / 实体页 / 文件夹）| `app/models/wiki/wiki_route_metric.py`、`app/services/wiki_metrics_service.py`、`app/api/wiki/router.py`、`frontend/src/components/chat/{KnowledgeScopePicker,WikiScopePanel}.tsx` | `test_wiki_metrics.py`、`test_wiki_scope_filter.py` |
| **M3 收尾** | 健康度卡片 + e2e 集成测试 + 本文档 | `frontend/src/components/wiki/WikiRouteHealthCard.tsx`、`test_wiki_e2e_pipeline.py` | 共 **82 用例 / 0 失败** |

---

## 2. 决策来源（`knowledge_path_source`）

KnowledgeRouter 现支持 5 种来源，按优先级排列：

| source | 触发条件 | 是否调用 LLM |
|--------|----------|--------------|
| `disabled` | `wiki.first_enabled = false` 或 `intent_type != tool_use` | 否 |
| `scope` | 用户在前端 `KnowledgeScopePicker` 选了 `domains` / `wiki_slugs` | 否 |
| `rule` | `router_keywords_rag` / `router_keywords_wiki` 命中 | 否 |
| `llm` | 规则不命中，LLM 结构化分类成功 | **是**（fast 模型，max 80 token） |
| `fallback` | LLM 异常或返回非法 path | 否 |

**前置成本控制**：rule + scope + disabled 三种来源完全 0 LLM 调用，是常态路径；
仅模糊问题才进入 llm 分支。

---

## 3. 数据落库

新增表：`wiki_route_metrics`（每次 doc_worker 执行落一行）

```sql
CREATE TABLE IF NOT EXISTS wiki_route_metrics (
    id VARCHAR(36) PRIMARY KEY,
    workspace_id VARCHAR(64) NOT NULL,
    knowledge_path ENUM('rag','wiki','both') NOT NULL,
    knowledge_path_source VARCHAR(16) NOT NULL,
    knowledge_path_reason VARCHAR(255),
    user_query VARCHAR(200),
    chunks_used INT DEFAULT 0,
    wiki_chunks_count INT DEFAULT 0,
    wiki_chunks_used INT DEFAULT 0,
    has_wiki_gap TINYINT(1) DEFAULT 0,
    wiki_gap_task_id VARCHAR(36),
    latency_ms INT DEFAULT 0,
    stop_reason VARCHAR(64),
    session_id VARCHAR(64),
    message_id VARCHAR(64),
    user_id VARCHAR(64),
    created_at DATETIME(6) NOT NULL,
    INDEX idx_ws_created (workspace_id, created_at),
    INDEX idx_path (knowledge_path),
    INDEX idx_gap (has_wiki_gap)
);
```

迁移脚本：`backend/migrations/run_wiki_route_metrics_migration.py`

---

## 4. API 端点（M3.5 新增）

| 方法 | 路径 | 用途 |
|------|------|------|
| `GET` | `/api/wiki/metrics/route?days=7` | 路由健康度聚合（rag/wiki/both 命中率、Gap 率、平均时延） |
| `GET` | `/api/wiki/domains?sample_per_domain=5` | 按 domain 聚合实体页（KnowledgeScopePicker 一级 Tab 数据源） |

> 既有 `/api/wiki/pages`、`/api/wiki/pages/{slug}`、`/api/wiki/compile`、`/api/wiki/lint` 在 M3 不变。

---

## 5. 前端组件

| 组件 | 路径 | 说明 |
|------|------|------|
| `KnowledgeScopePicker` | `frontend/src/components/chat/KnowledgeScopePicker.tsx` | Tabs 切分「Wiki 范围 / 文档库范围」，徽标显示 Wiki 选中数 |
| `WikiScopePanel` | `frontend/src/components/chat/WikiScopePanel.tsx` | Wiki 域 chips（多选）+ 实体页搜索 / 勾选 |
| `WikiRouteHealthCard` | `frontend/src/components/wiki/WikiRouteHealthCard.tsx` | 管理员在 Wiki 视图右侧空白态看到的健康度卡片，支持 7/14/30 天窗口 |

`DocScope` 类型扩展：

```ts
interface DocScope {
    folder_ids: string[]
    file_ids: string[]
    include_subfolders: boolean
    domains: string[]      // M3.5 新增
    wiki_slugs: string[]   // M3.5 新增
}
```

---

## 6. 配置开关（`backend/app/config.py: WikiSettings`）

| 字段 | 默认 | 说明 |
|------|------|------|
| `first_enabled` | `false` | **总开关**。关闭时 KnowledgeRouter 直通，行为与 M2 完全一致 |
| `router_model` | `qwen3.5-flash` | 路由判定模型（仅 llm 分支调用） |
| `router_keywords_rag` | （内置 20+ 词） | 命中即走 RAG，零 LLM 成本 |
| `router_keywords_wiki` | （内置 18+ 词） | 命中即走 Wiki，零 LLM 成本 |
| `router_fallback_path` | `both` | LLM 异常时回退路径，可选 `rag/wiki/both` |
| `index_load_top_k` | `8` | wiki_navigate 单次最多装载几页 |
| `max_page_chars` | `12000` | 单实体页字数硬上限 |
| `index_summary_max_chars` | `80` | INDEX 中每条 summary 的截断长度 |
| `lint_enabled` | `true` | 健康巡检功能（M2 引入，M3 不变） |

---

## 7. 运行手册

### 7.1 启用 Wiki-First

```bash
# 1) 建表
docker-compose exec admin-backend python -m migrations.run_wiki_route_metrics_migration

# 2) 打开开关（任选其一）
#    a. 修改 backend/app/config.py 设 first_enabled=True 后重启容器
#    b. 通过环境变量：WIKI__FIRST_ENABLED=true（pydantic-settings 风格）

# 3) 验证路由活跃
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8030/api/wiki/metrics/route?days=7"
```

### 7.2 端到端冒烟（无外网）

```bash
docker-compose exec admin-backend python -m pytest \
    app/tests/test_wiki_e2e_pipeline.py \
    -v --tb=short -p no:cacheprovider
```

预期：5 用例全部通过（规则命中 wiki / 规则命中 rag / scope 强制 wiki / LLM fallback / wiki_gap_signal 透传）。

### 7.3 全量 M3 回归

```bash
docker-compose exec admin-backend python -m pytest \
    app/tests/test_knowledge_router.py \
    app/tests/test_wiki_navigator.py \
    app/tests/test_doc_worker_routing.py \
    app/tests/test_wiki_gap_and_citations.py \
    app/tests/test_wiki_metrics.py \
    app/tests/test_wiki_scope_filter.py \
    app/tests/test_wiki_e2e_pipeline.py \
    -q --tb=short
```

预期：**82 passed in <10 s**。

---

## 8. 关键设计决策

1. **零回归优先**：`first_enabled=false` 时所有改动旁路退化，链路与 M2 字节级一致
2. **规则前置 / LLM 后置**：常见关键词 0 LLM 成本，模糊问题才走 fast 模型分类（max 80 token）
3. **doc_worker.meta.wiki_gap_signal 契约稳定**：M3.4 修复保留，所有 return 路径都带这个键，Executor 埋点不会漏行
4. **跨方言聚合**：`get_route_summary` 用 SQLAlchemy `case(...)` 表达式，MySQL/SQLite/PG 通用
5. **显式范围零 LLM**：`wiki_slugs` 非空 → 直接装载；`domains` 非空 → 收窄 INDEX；都跳过 LLM 选页
6. **doc_scope 限额**：`MAX_WIKI_DOMAINS=50`、`MAX_WIKI_SLUGS=200`，避免上下文膨胀
7. **埋点 fire-and-forget**：`Executor._record_wiki_route_metrics` 失败不影响主流程

---

## 9. 关键文件索引

### 后端

```
app/
├─ supervisor/
│  ├─ nodes/
│  │  ├─ knowledge_router.py     # M3.1 路由判定 + M3.5 scope 强制规则
│  │  ├─ executor.py             # M3.5 _record_wiki_route_metrics 埋点
│  │  └─ synthesizer.py          # M3.4 wiki_gap_signal 检测 + citation 透传
│  └─ state.py                   # SupervisorState 新增 knowledge_path / source / reason / wiki_gap_signal
├─ core/wiki/
│  └─ navigator.py               # M3.2 + M3.5 (domains/wiki_slugs 过滤)
├─ skills/
│  └─ doc_skill.py               # wiki_navigate 透传新参数
├─ tools/
│  └─ doc_tool.py                # 双路径分派 + scope 透传
├─ models/wiki/
│  └─ wiki_route_metric.py       # M3.5 ORM
├─ services/
│  ├─ wiki_metrics_service.py    # M3.5 聚合 service
│  └─ doc_scope.py               # normalize_doc_scope 扩展 domains/wiki_slugs
├─ api/wiki/
│  ├─ router.py                  # +/metrics/route +/domains
│  └─ schemas.py                 # WikiRouteSummary / WikiDomainListResponse
└─ tests/
   ├─ test_knowledge_router.py
   ├─ test_wiki_navigator.py
   ├─ test_doc_worker_routing.py
   ├─ test_wiki_gap_and_citations.py
   ├─ test_wiki_metrics.py
   ├─ test_wiki_scope_filter.py
   └─ test_wiki_e2e_pipeline.py

migrations/
└─ run_wiki_route_metrics_migration.py
```

### 前端

```
src/
├─ components/
│  ├─ chat/
│  │  ├─ KnowledgeScopePicker.tsx  # Tabs 升级
│  │  └─ WikiScopePanel.tsx        # Wiki 域 + 实体页选择
│  └─ wiki/
│     ├─ WikiView.tsx              # 嵌入健康度卡片
│     ├─ WikiIndexView.tsx
│     ├─ WikiPageDetailView.tsx
│     └─ WikiRouteHealthCard.tsx   # M3 收尾
├─ services/
│  └─ wikiService.ts               # +listDomains/+getRouteMetrics
└─ types/
   └─ docScope.ts                  # +domains/+wiki_slugs
```

---

## 10. 下一步：M4 治理 + 配额

待启动事项（不属于本次 M3 范围，仅作前瞻）：

- [ ] **健康巡检报告**：孤儿页、断链、循环引用、冲突 slug
- [ ] **配额纳管**：每工作区 `max_pages` / `max_links_per_page` 上限纳入 admin UI
- [ ] **修订审计**：`wiki_revision` 表的 diff 视图
- [ ] **Wiki Gap 任务自动化**：Synthesizer 检测到 gap → 写 `wiki_compile_task` → 异步编译 → 回流

---

**总结：M3 已完成全部规划项，82 用例 / 0 失败 / 0 回归，可以投产。**
