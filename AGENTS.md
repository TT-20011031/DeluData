# AGENTS.md

本文件是 Codex 在本仓库内工作的项目说明。它基于当前目录结构、主要配置文件和关键源码入口整理；后续改动代码时优先遵循这里的约定。

## 项目概览

DeluData 是一个企业级 AI 数据问答与智能分析平台，围绕自然语言问数、Text-to-SQL、知识库 RAG、Office 文档生成/编辑、图表生成、Wiki-First 知识治理，以及面向展厅/科普场景的互动体验系统展开。

整体架构是前后端分离：

- 后端：FastAPI + Pydantic + LangGraph + SQLAlchemy + ChromaDB + Redis。
- 主前端：React 19 + Vite + TypeScript + Zustand + Radix UI/Shadcn 风格组件。
- 平台管理端：React 19 + Vite，面向平台管理员。
- Kiosk 体验端：React 19 + Vite，面向展厅/科普互动大屏。
- 部署：Docker Compose 编排 MySQL、Redis、双后端、多个前端、后台 worker 和 Nginx 网关。

## 常用命令

### 后端开发

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# 管理后台 API，默认端口 8000
uvicorn app.entrypoints.admin_main:app --reload --host 0.0.0.0 --port 8000

# 体验端 API，默认端口 8001
uvicorn app.entrypoints.experience_main:app --reload --host 0.0.0.0 --port 8001

# 文档入库后台任务
python -m app.entrypoints.ingestion_worker

# Wiki 编译后台任务
python -m app.entrypoints.wiki_compile_worker
```

### 前端开发

```bash
# 租户/管理工作台
cd frontend
npm install
npm run dev
npm run build
npm run lint
npm run test

# 平台管理端
cd frontend-platform
npm install
npm run dev
npm run build
npm run lint

# Kiosk 体验端
cd kiosk-frontend
npm install
npm run dev
npm run build
npm run lint
```

主前端的 `dev` 和 `build` 会先执行 `sync:pdf-assets`，用于同步 PDF worker、cmaps、standard fonts 等资源。平台端和 Kiosk 端没有测试脚本，提交前至少运行对应 `build` 和 `lint`。

### 测试

```bash
# 后端
cd backend
pytest tests/ -v

# 主前端
cd frontend
npm run test
```

按改动范围选择最小有效测试集合。跨模块改动、权限/RAG/SQL/Office/图表相关改动，应优先补充或运行对应后端测试。

### Windows 快速启动

```bash
start_app.bat
```

脚本会启动：

- Admin Backend：`http://localhost:8000`
- Experience Backend：`http://localhost:8001`
- 主前端：`http://localhost:3000`
- 平台管理前端：`http://localhost:5173/login`
- Kiosk 前端：`http://localhost:5174`

默认平台管理员账号在脚本中标注为 `admin / admin123`。脚本会主动释放 8000、8001、3000、5173、5174 等端口；已有服务运行时谨慎使用。

## 顶层目录

- `backend/`：FastAPI 后端、LangGraph 编排、RAG、SQL、Office、Wiki、体验服务、迁移和测试。
- `frontend/`：租户/管理工作台，包含聊天、知识库、数据库配置、权限、模板、扩展场景、博物馆导览等页面。
- `frontend-platform/`：平台管理端，包含租户、平台管理员、知识治理、工作区数据库白名单等页面。
- `kiosk-frontend/`：科普/展厅互动体验端，包含激活页、吸引页、问答、答题、奖励等体验壳层。
- `deploy/nginx/`：Docker 部署时的 Nginx 网关配置。
- `docs/`：架构、部署、Wiki 治理、多模态 RAG、博物馆/科普体验等设计文档。
- `mcp-langchain/`：LangChain/MCP 流式能力相关实验或辅助服务。

## 后端结构

后端代码集中在 `backend/app/`。

- `entrypoints/`：进程入口。
  - `admin_main.py`：管理后台 API 入口。
  - `experience_main.py`：体验端 API 入口。
  - `ingestion_worker.py`：知识库入库 worker。
  - `wiki_compile_worker.py`：Wiki 编译 worker。
- `api/`：FastAPI 路由。
  - `api_router_admin.py` 组合管理端路由。
  - `api_router_experience.py` 组合体验端路由。
  - 管理端主要子域包括 `auth`、`chat`、`knowledge`、`database`、`files`、`config`、`organization`、`platform`、`wiki`、`voice`、`sandbox`、`science_admin`。
  - 体验端通过 `app.experience.api.router` 组合 guest-facing API。
- `supervisor/`：LangGraph Supervisor 状态机。
  - `graph.py` 组装节点和边。
  - `edges.py` 定义条件路由。
  - `state.py` 定义 `SupervisorState`。
  - `nodes/` 包含 intent classifier、knowledge router、planner、executor、router agent、synthesizer、summarizer、direct execute、suspend 等节点。
- `agents/`：Worker 实现。
  - `sql_worker.py`：SQL 生成与执行。
  - `office_worker.py`：Word/Excel/模板类任务。
  - `office/handlers/`：Office 创建、Excel 创建、模板填充等策略处理器。
  - `reflector/`：质量判断与反思相关逻辑。
- `skills/`：底层能力封装。
  - `base.py`：Skill 基类。
  - `schema_skill.py`：数据源/元数据能力。
  - `sql_skill.py`：SQL 执行与安全校验。
  - `doc_skill.py`：知识库检索。
  - `python_skill.py`：Python 沙箱类能力。
  - `xiyan_skill.py`、`xiyan_mock.py`：析言/问数相关能力。
- `tools/`：可被 Worker 调用的工具，包括 SQL、文档、图表、Office、文件检查等。
- `core/`：基础设施。
  - `db/`：数据库连接、只读执行器、LangGraph checkpointer。
  - `llm/`：LLM/VLM/Embedding 客户端与 Prompt 管理。
  - `rag/`：文档解析、OCR、混合检索、重排、语义分块、PDF 多模态、PageIndex。
  - `security/`：认证、RBAC、路径安全、数据作用域。
  - `storage/`、`utils/`：存储和通用工具。
- `services/`：业务服务。
  - 重点包括 `chat_service.py`、`ingestion_service.py`、`workspace_readiness_service.py`、`db_whitelist_service.py`、`doc_scope.py`、`wiki_service.py`、`wiki_compile_queue_service.py`、`template_service.py`、`skill_service.py`。
- `models/`：数据库/领域模型，覆盖 auth、config、knowledge、wiki、platform、system、common 等。
- `experience/`、`museum/`：科普/展厅体验服务，包含导览、语音、答题、奖励、画像、商品/展品等能力。
- `prompts/`：YAML Prompt 模板，包含 supervisor、workers、sql_worker、office、analyst、wiki、query_rewrite 等。
- `migrations/`：数据库迁移脚本。
- `tests/`：pytest 测试，覆盖聊天、RAG、Wiki、权限、模板、Office、体验服务等。

## LangGraph 工作流

核心图由 `backend/app/supervisor/graph.py` 定义：

```text
intent_classifier
  -> knowledge_router
  -> planner
  -> human_review / executor / suspend / synthesizer
  -> router_agent
  -> synthesizer
  -> summarizer
  -> END
```

特殊路径：

- 闲聊或直接回答：`intent_classifier -> synthesizer`。
- 直连模式：`intent_classifier -> direct_execute -> synthesizer`。
- 需要人工确认：`planner -> human_review -> executor`。
- 中断/恢复：`suspend` 根据恢复后的状态回到 `planner` 或 `executor`。
- Worker 执行后统一进入 `router_agent`，判断继续、重试、切换 worker 或结束。

`execution_mode` 支持：

- `auto`
- `rag_only`
- `sql_only`
- `chart_only`
- `office_only`

Worker 分类在 `backend/app/worker_categories.py`：

- 提取类：`sql_worker`、`doc_worker`、`inspect_file`
- 终结类：`chart_worker`、`office_worker`
- 工具类：`finish`

## 关键业务能力

### Text-to-SQL

- 主要由 `sql_worker`、`schema_skill`、`sql_skill`、数据库配置和白名单服务组成。
- 安全重点是工作区级数据作用域、数据库白名单、只读执行、防危险 SQL。
- 与 `memory_dfs` 结合，把查询结果传给后续图表、Office 或综合回答阶段。

### RAG 与知识库

- 文档入库在 `ingestion_service.py`、`core/rag/document_ingestor.py` 等模块。
- 检索能力包括语义分块、混合检索、BM25、重排、上下文扩展、PDF 图片/页面索引和 OCR。
- `doc_scope.py` 负责合并会话级和步骤级文档范围。
- Wiki-First 路由由 `knowledge_router`、`wiki_service`、`wiki_metrics_service`、`wiki_compile_queue_service` 支撑。

### Office 与图表

- `office_worker.py` 负责 Word/Excel/模板类任务。
- `agents/office/handlers/` 使用策略处理创建、Excel 生成和模板填充。
- `tools/chart_tool.py` 负责根据数据生成可视化产物，并通过 SSE artifact 返回前端。
- `libs/word_mcp/` 和 `libs/excel_mcp/` 提供本地 Word/Excel 操作封装。

### 科普/展厅体验

- 后端体验入口是 `app.entrypoints.experience_main`。
- `app/experience/` 管理激活、语音配置、画像、问答、答题、奖励等流程。
- `app/museum/` 包含导览、ASR/TTS、人物识别、场景分析、商品/展品服务。
- `kiosk-frontend/` 未配置设备 token 时进入激活页，配置后进入体验壳层。

## 前端结构

### 主前端 `frontend/`

- `src/router`：页面路由和认证/功能开关守卫。
- `src/pages/`：主要页面。
  - `ChatPage.tsx`
  - `KnowledgeBasePage.tsx`
  - `DatabaseConfigPage.tsx`
  - `UserManagePage.tsx`
  - `RoleManagePage.tsx`
  - `PermissionsPage.tsx`
  - `DepartmentPage.tsx`
  - `AgentConfigPage.tsx`
  - `TemplateEditorPage.tsx`
  - `ExtendConfigPage.tsx`
  - `ExtendScenesPage.tsx`
  - `SystemSettingsPage.tsx`
- `src/museum/`：博物馆导览和商店等扩展场景页面。
- `src/components/`：聊天、知识库、Wiki、UI 组件等。
- `src/stores/`：Zustand 状态，包括聊天、认证、数据库、上传、布局等。
- `src/api/`：API 客户端函数。
- `src/config.ts`：`API_BASE_URL`、`SSE_BASE_URL`、SSE 事件名和通道常量。

开发环境中，如果前端运行在 loopback host 且 API 指向另一个 loopback origin，`src/config.ts` 会把 API base 归一化成同源路径，便于 Vite 代理和本地调试。

### 平台管理前端 `frontend-platform/`

- 页面包括 `Dashboard`、`Tenants`、`KnowledgeGovernance`、`Admins`、`Settings`、`WorkspaceDbWhitelist`、`Login`。
- API 基础路径为 `/api/platform`。
- 登录 token 存储在 `localStorage.platform_token`。
- Docker 构建时支持 `VITE_APP_BASE=/platform/`。

### Kiosk 前端 `kiosk-frontend/`

- 入口 `src/App.tsx` 根据设备 token 判断显示激活页或体验壳层。
- 主要目录包括 `src/api`、`src/components`、`src/config`、`src/hooks`、`src/pages`、`src/store`、`src/utils`。
- 依赖包括 MediaPipe、Framer Motion、QRCode、Zustand。
- 开发脚本里默认注入 `VITE_DEVICE_TOKEN=dev_device_token_001`。

## 配置

后端配置集中在 `backend/app/config.py`，使用 `pydantic-settings` 从环境变量和 `.env` 读取。

常见配置项：

- 数据库：`DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME`
- 应用端口：`APP_ADMIN_PORT`、`APP_EXPERIENCE_PORT`
- LLM：`DASHSCOPE_API_KEY`、`LLM_BASE_URL`、各类模型配置
- Chroma：`CHROMA_PERSIST_DIR`
- Redis：用于缓存、队列和部分多模态能力
- RAG/OCR：`RAG_OCR_DEVICE`、`RAG_OCR_PARALLEL_PAGES`、`RAG_OCR_DPI` 等
- Experience：`EXPERIENCE_*`，控制 Kiosk 问答、画像、激活、SSE 心跳等体验端行为

前端配置：

- 主前端使用 `VITE_API_BASE_URL`、`VITE_SSE_BASE_URL`。
- Kiosk 前端使用 `VITE_API_BASE_URL`、`VITE_DEVICE_TOKEN`。
- 平台前端默认走 `/api/platform`。

## Docker 部署

`docker-compose.yml` 编排以下服务：

- `mysql`：MySQL 8.0，默认数据库 `DeluData`。
- `redis`：Redis 7。
- `admin-backend`：管理后端，容器内端口 8000。
- `experience-backend`：体验后端，容器内端口 8001。
- `ingestion-worker`：文档入库 worker。
- `wiki-compile-worker`、`wiki-compile-worker-2`：Wiki 编译 worker，可并发处理不同任务。
- `frontend-admin`：主前端。
- `frontend-experience`：Kiosk 前端。
- `frontend-platform`：平台管理前端。
- `nginx`：统一网关，对外端口为 8030、8031、8032。

## 开发约定

- 后端优先使用 async/await；IO、数据库、外部 API 调用不要阻塞事件循环。
- 配置必须通过 `config.py` 或环境变量读取，避免硬编码密钥、端口、模型名和路径。
- 新增 API 时先确认属于 admin 进程还是 experience 进程，再挂到对应 `api_router_admin.py` 或 `api_router_experience.py`。
- 新增 LangGraph 节点时同步检查 `graph.py`、`edges.py`、`SupervisorState` 和 SSE 事件。
- 新增 Worker 时同步更新 `worker_categories.py`、Planner/worker Prompt 和必要的前端任务展示。
- 新增 Skill 应继承 `BaseSkill`，并保持输入输出结构稳定，便于 Planner 和 Executor 编排。
- 涉及 SQL 的改动必须保留工作区隔离、白名单、只读执行和危险操作拦截。
- 涉及知识库的改动要注意文档软删除、工作区隔离、doc_scope、Wiki/RAG 路由和入库队列。
- 前端页面应复用现有组件、store、API 客户端和视觉风格，不随意引入新的 UI 范式。
- 修改文件生成、Office、图表、RAG 或权限相关逻辑时，应补充或更新后端测试。
- 不要把 `frontend/dist/`、`backend/data/chroma/`、`__pycache__/` 等生成产物当作源码依据来修改。

## 测试重点

当前 `backend/tests/` 已覆盖大量回归场景。常见相关测试方向：

- 聊天和 Supervisor 流程：`test_supervisor_flow.py`、`test_chat_*`
- SQL/数据库安全：`test_db_whitelist_service.py`、`test_database_whitelist_status.py`
- 知识库/RAG：`test_rag_*`、`test_pdf_*`、`test_pageindex_*`
- Wiki 治理：`test_workspace_knowledge_governance_service.py`
- Office/模板：`test_office_excel_generation.py`、`test_template_*`
- 体验端：`test_science_experience_services.py`、`test_quiz_progression.py`
- 权限与租户：`tests/integration/test_tenant_security.py`

提交前根据改动范围选择最小但有效的测试集合；跨模块改动应运行更完整的 pytest。

## 注意事项

- 仓库里部分历史 Markdown 或注释文本存在编码损坏；修改时新写内容统一使用 UTF-8。
- Docker 默认数据库密码和本地脚本默认账号仅适合开发环境，生产部署必须改环境变量和密钥。
- 当前工作目录没有检测到 `.git` 元数据时，无法依赖 `git status` 判断用户改动；编辑前要更加谨慎，只改任务相关文件。
- 大范围重构前先阅读相关服务、模型、路由、测试四类文件，避免只改入口导致运行时契约破坏。
