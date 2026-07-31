# 第一场演示·演示前自检（≈ 3 分钟）

> 适用范围：M1 编译实体页 + M3 路由对比，本地 docker-compose 环境。

---

## 1. 服务在线

```bash
docker-compose ps
```

与本场演示相关的关键容器（皆需 `Up`）：

- `mysql` · 实体页 / 路由埋点落库
- `redis` · 会话与队列
- `admin-backend` · 后端 API（FastAPI on :8000）
- `frontend-admin` · 管理后台前端
- `nginx` · 网关（宿主访问入口 `:8030`）
- `ingestion-worker` · 文档切片入库（上传后需它处理）
- `wiki-compile-worker` · Wiki 后台编译队列（本场“全量编译”走同步可不依赖它，但建议并起）

> 项目使用嵌入式 ChromaDB（持久化到 `./data/chroma`），**没有**独立的 `chroma` 容器。

如有 `Exit`：

```bash
docker-compose up -d
```

---

## 2. Wiki 相关表迁移

```bash
docker-compose exec -w /app admin-backend python -m migrations.run_wiki_tables_migration
docker-compose exec -w /app admin-backend python -m migrations.run_wiki_route_metrics_migration
```

- `-w /app` 显式指定工作目录为 backend 根（容器内 `WORKDIR`），与迁移脚本 docstring 中 `cd backend` 的要求一致
- 期望末行输出含 `migration completed` / `✅` / `done` 之一；重复执行幂等（`CREATE TABLE IF NOT EXISTS`）
- 如报 `ModuleNotFoundError: No module named 'migrations'`，检查容器 cwd 是否为 `/app`（`docker-compose exec admin-backend pwd`）

---

## 3. 打开 Wiki-First 主开关

编辑 `backend/.env`（或 docker-compose 注入的环境文件），加入或修改：

```ini
WIKI_FIRST_ENABLED=true
```

> 变量名是**单下划线**。依据：`backend/app/config.py` 中 `WikiSettings.model_config = SettingsConfigDict(env_prefix="WIKI_", ...)`，prefix `WIKI_` + 字段 `first_enabled` 上拼 → `WIKI_FIRST_ENABLED`；该模型未启用 `env_nested_delimiter`，双下划线写法不生效。

重启后端让环境变量生效：

```bash
docker-compose up -d admin-backend wiki-compile-worker
```

> 开关关闭时 KnowledgeRouter 节点直通，不会做三段式判定，本场所有 M3 演示都看不到效果。

---

## 4. 管理员账号到位

演示账号必须有 **admin 角色**（INDEX 视图的"全量编译"按钮、健康度卡片仅管理员可见）。

获取方式二选一：

- **新注册账号 + 升权**：使用前端注册一个新账号 → 平台管理员后台把 role 改为 `admin`
- **直接用现有 admin**：登录任一现有 admin，在演示前清空它名下工作区的 wiki 数据：

```sql
-- 谨慎操作：仅 demo workspace
DELETE FROM wiki_links WHERE workspace_id = '<demo-ws-id>';
DELETE FROM wiki_page_sources WHERE workspace_id = '<demo-ws-id>';
DELETE FROM wiki_revisions WHERE workspace_id = '<demo-ws-id>';
DELETE FROM wiki_pages WHERE workspace_id = '<demo-ws-id>';
DELETE FROM wiki_compile_tasks WHERE workspace_id = '<demo-ws-id>';
```

---

## 5. 样本文件就绪

确认以下 3 份样本可以拖拽上传：

```
docs/demo/part1/sample/01-overview.md
docs/demo/part1/sample/02-architecture.md
docs/demo/part1/sample/03-modules.md
```

> 若需要在远程演示，提前把这三份拷到演示者本地。

---

## 自检完成判断

满足以下三条即可开始演示：

- [ ] §1 列出的关键容器都 `Up`
- [ ] `WIKI_FIRST_ENABLED=true` 已写入 `.env`，且 `docker-compose exec admin-backend env | grep WIKI_FIRST_ENABLED` 能输出该变量
- [ ] 用 admin 账号登录，能看到知识库页右上角“Wiki 视图”切换入口
