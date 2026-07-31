---
title: DeluData 项目知识索引
type: project-moc
tags:
  - 工作/项目
  - 技术/AI
  - MOC
created: 2026-07-16
updated: 2026-07-16
status: active
source: S:/AiData/DeluData_pro
---

# DeluData 项目知识索引

> [!summary]
> DeluData 是一个多租户企业级 AI 数据问答与智能分析平台。它以自然语言交互为入口，把 Text-to-SQL、知识库 RAG、Wiki-First、可信语义治理、Office/图表生成和展厅 Kiosk 体验统一到受权限控制的执行体系中。

## 知识地图

1. [[DeluData-01-项目总览与核心价值]]：产品定位、核心能力和演进方向。
2. [[DeluData-02-系统架构与模块边界]]：三个前端、双后端、后台 Worker、数据设施和网关。
3. [[DeluData-03-LangGraph-Supervisor执行方法]]：从意图识别、规划、执行到综合回答的状态机。
4. [[DeluData-04-Text-to-SQL与可信语义治理]]：数据库问数、语义模型、证据治理和 SQL 安全。
5. [[DeluData-05-RAG-Wiki-First与知识治理]]：文档入库、混合检索、引用、Wiki 编译和治理闭环。
6. [[DeluData-06-多租户权限与安全模型]]：Workspace、Capability Scope、文档范围和设备鉴权。
7. [[DeluData-07-前端产品形态与接口契约]]：主工作台、平台端、Kiosk 和 SSE 契约。
8. [[DeluData-08-构建部署测试与维护]]：本地启动、Docker、Nginx、测试和排障。

## 推荐阅读顺序

- 初次接触：01 → 02 → 03。
- 修改聊天或 Agent：03 → 04/05 → 06。
- 修改知识库：05 → 06 → 08。
- 修改权限或语义治理：06 → 04 → 08。
- 部署排障：02 → 08。
- 修改 Kiosk：07 → 06 → 02。

## 核心源码入口

- `backend/app/entrypoints/admin_main.py`：Admin Backend 入口。
- `backend/app/entrypoints/experience_main.py`：Experience Backend 入口。
- `backend/app/entrypoints/common.py`：公共生命周期、中间件和基础设施初始化。
- `backend/app/supervisor/graph.py`：LangGraph Supervisor 图。
- `backend/app/services/chat_service.py`：API 与 Supervisor 之间的业务门面。
- `backend/app/tools/registry.py`：Worker 工具注册中心。
- `frontend/src/router/index.tsx`：租户主工作台路由。
- `frontend/src/services/chatService.ts`：聊天 API 契约。
- `kiosk-frontend/src/api/client.ts`：Kiosk API 客户端。
- `docker-compose.yml`：容器编排。
- `deploy/nginx/gateway.conf`：统一网关。

## 当前实现快照

- Web 进程：Admin Backend、Experience Backend。
- 后台进程：Ingestion Worker、Semantic Governance Worker、两个 Wiki Compile Worker、Knowledge MCP。
- 数据设施：MySQL、Redis、ChromaDB、文件与沙箱存储。
- Supervisor 工具：`sql_worker`、`doc_worker`、`chart_worker`、`office_worker`、`inspect_file`。
- 后端测试同时分布于 `backend/tests/` 和 `backend/app/tests/`，当前发现 83 个测试文件。
- 挂载目录根部没有 `.git`；SSHFS 环境不能依赖本机 `git status` 判断服务器改动。

## 维护总原则

1. 先判断功能属于 Admin、Platform 还是 Experience。
2. 数据能力必须在 Workspace、Capability、数据范围和只读约束之后运行。
3. LangGraph 改动同步检查 State、Nodes、Edges、SSE 和 Checkpointer。
4. RAG 改动同步考虑软删除、Doc Scope、Wiki 路由、入库队列和引用契约。
5. SQL、Office、图表、权限和 RAG 的跨模块改动应配套回归测试。
6. 以当前源码和迁移为准；README 与 AGENTS.md 的部分内容落后于实现。
