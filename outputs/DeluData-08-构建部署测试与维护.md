---
title: DeluData 构建部署测试与维护
type: operations-notes
tags:
  - 工作/项目
  - 技术/服务器
  - 技术/工具
created: 2026-07-16
updated: 2026-07-16
status: active
---

# DeluData：构建、部署、测试与维护

返回：[[DeluData-项目知识索引]]

## 本地后端

```powershell
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.entrypoints.admin_main:app --reload --host 0.0.0.0 --port 8000
uvicorn app.entrypoints.experience_main:app --reload --host 0.0.0.0 --port 8001
```

后台任务分别运行：

```powershell
python -m app.entrypoints.ingestion_worker
python -m app.entrypoints.wiki_compile_worker
python -m app.entrypoints.semantic_governance_worker
python -m app.mcp.knowledge_server
```

## 前端构建

```powershell
cd frontend
npm install
npm run dev
npm run build
npm run lint
npm run test
```

平台端和 Kiosk 使用 `npm run build`、`npm run lint`，目前没有独立测试脚本。主前端的 dev/build 会先执行 `sync:pdf-assets`。

`start_app.bat` 可快速启动本地五个端口，但会主动释放 8000、8001、3000、5173、5174；已有服务运行时不要直接执行。

## Docker Compose

主要服务：

- MySQL 8、Redis 7。
- Admin Backend、Experience Backend。
- Ingestion Worker、Semantic Governance Worker。
- 两个 Wiki Compile Worker。
- Knowledge MCP。
- 三个前端与 Nginx。

后端服务使用公共 Build、环境文件、数据库/Redis 环境变量和数据卷。Web 与 Worker 共享业务镜像，但命令不同。

## Nginx

- `8030`：Kiosk/Experience。
- `8031`：主工作台/Admin API/Platform 子路径/MCP。
- `8032`：独立 Platform。

SSE、MCP 和长任务代理关闭 buffering，并设置更长的读超时。外层 HTTPS 代理的 `X-Forwarded-Proto` 会被保留。

## 配置方法

后端通过 `pydantic-settings` 从环境变量与 `.env` 读取。主要分组：Database、LLM、App、Experience、RAG、Sandbox、Storage、OSS、MCP、Supervisor、Redis、VLM、Skill、Wiki。

原则：密钥、模型、端口、路径和运行开关进入配置，不硬编码。生产必须覆盖 Compose 中的开发默认密码和 Token。

## 数据库迁移

`backend/migrations/` 同时包含 SQL、Python Runner 和检查脚本。最近迁移集中在语义基线、证据治理、行级权限、语义访问策略和 Authorization V2。

迁移前：备份数据库、确认目标版本和环境；迁移后：检查表、索引、约束、数据回填和回滚方案。不要仅凭 ORM `create_all` 认为生产 Schema 已升级。

## 测试策略

```powershell
cd backend
pytest tests/ -v
pytest app/tests/ -v
```

测试实际分布在两处，执行全量回归时不能遗漏 `app/tests/`。按改动范围选择最小有效集合：

- Supervisor/聊天：`test_supervisor_flow.py`、`test_chat_*`、`test_reasoning_*`。
- SQL/语义：`test_sql_*`、`test_semantic_*`、`test_db_whitelist_*`。
- RAG/Wiki：`test_rag_*`、`test_pdf_*`、`test_wiki_*`。
- Office/模板：`test_office_*`、`test_template_*`。
- 权限：`test_authorization_v2.py`、`test_tenant_security.py`。
- Experience：`test_science_experience_services.py`、`test_quiz_progression.py`。

## 维护与排障顺序

1. 确认入口进程和外部端口。
2. 检查 Workspace、用户、设备和 Feature 状态。
3. 检查 MySQL、Redis、Chroma 和文件存储。
4. 查看请求是否进入 API、Service、Supervisor/Worker。
5. 用 session/plan/round/step 或 Run Token 串联日志。
6. 检查后台队列是否 Pending、Running、超时或失去 Lease。
7. 运行最小相关测试，再扩大回归范围。

## 挂载环境注意事项

- 当前项目通过 SSHFS 挂载，根部没有可用 `.git` 元数据。
- 本机 Git 对挂载根路径可能报 `failed to stat ... Function not implemented`。
- 修改前不能依赖 `git status` 识别服务器端用户改动，应精确限定文件。
- 避免扫描或修改 `dist/`、Chroma 数据、`__pycache__/` 和 pytest 临时目录。
- 大文件扫描和并发递归命令可能受 SSHFS 延迟影响，优先使用范围明确的命令。

相关：[[DeluData-02-系统架构与模块边界]]、[[DeluData-07-前端产品形态与接口契约]]
