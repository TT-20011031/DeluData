<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/React-19+-61DAFB?logo=react&logoColor=black" alt="React">
  <img src="https://img.shields.io/badge/LangGraph-0.2+-FF6F00?logo=langchain&logoColor=white" alt="LangGraph">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
</p>

<h1 align="center">🧠 DeluData 智能问数系统</h1>

<p align="center">
  <strong>企业级 AI 驱动的数据问答与智能分析平台</strong><br>
  基于 E-SOA 架构 · LangGraph 多智能体编排 · 多模态 RAG · Text-to-SQL
</p>

---

## ✨ 核心特性

| 特性 | 描述 |
|------|------|
| 🤖 **多智能体协作** | 基于 LangGraph 的 Supervisor-Worker 架构，智能分配任务给专业 Agent |
| 📊 **Text-to-SQL** | 自然语言转 SQL，支持复杂多表查询，内置语义防火墙 |
| 📚 **多模态 RAG** | 支持 PDF/Word/Excel 文档解析，图片 VLM 智能描述，混合检索+重排序 |
| 📈 **智能可视化** | 自动生成 ECharts 图表，支持导出 PNG/PDF |
| 📝 **智能文件处理** | 自动生成 Word/Excel 报告，支持模板填充和精准修改 |
| 🔐 **企业级安全** | 双重围栏机制（语义防火墙+执行防火墙），RBAC 权限控制 |
| ⚡ **实时交互** | SSE 双轨事件流，流式输出思维链与执行状态 |

---

## 🎯 核心功能详解

### 📊 Text-to-SQL 智能问数

将用户的自然语言问题自动转换为 SQL 查询，支持：

- **多表联合查询** - 自动识别表间关系并构建 JOIN
- **聚合与分组** - 智能识别统计需求（SUM/AVG/COUNT/GROUP BY）
- **时间范围过滤** - 理解"上个月"、"今年Q1"等自然语言时间
- **结果可视化** - 查询结果自动生成 ECharts 图表

```
用户：本季度各部门销售额对比
系统：自动生成 SQL → 执行查询 → 生成柱状图
```

### 📚 多模态 RAG 知识库

企业级文档问答系统，支持：

| 文档类型 | 支持格式 | 特殊能力 |
|----------|----------|----------|
| 📄 PDF | .pdf | 页码定位、图片提取、VLM 描述 |
| 📝 Word | .docx | 结构解析、段落提取 |
| 📊 Excel | .xlsx | 表格数据理解 |
| 📑 Markdown | .md | 标题层级结构 |
| 📃 纯文本 | .txt | 语义切分 |

**高级检索能力：**

1. **语义切分** - 基于 Embedding 相似度的智能文档分块
2. **混合检索** - Dense (向量) + Sparse (BM25) 双路召回
3. **重排序** - Cross-Encoder 精排，提升召回精度
4. **上下文扩展** - 父子索引，自动补充前后文
5. **图片关联** - VLM 生成图片描述，支持以图搜文

### 📝 智能文件处理 (OfficeWorker)

基于策略模式的办公文件处理智能体：

| 模式 | 触发条件 | 能力 |
|------|----------|------|
| **Creation** | 无文件 + 生成类关键词 | Markdown → Word 快速生成报告 |
| **Editing** | 有文件 + 修改类关键词 | ReAct/MCP 精准修改文档内容 |

**支持场景：**

- 📃 **报告生成** - "帮我写一份本月销售分析报告"
- 📊 **数据导出** - "把查询结果导出成 Excel"
- ✏️ **文档修改** - "把报告里的日期改成下周一"
- 📋 **模板填充** - "用这份模板生成合同"

### 📈 智能可视化 (ChartTool)

基于 ** LLM 生成 HTML** 的数据可视化方案：

```
查询结果 (memory_dfs)
        ↓
┌───────────────────────────────────────┐
│  LLM 自由发挥                          │
│  • 分析数据结构 (DataFrame/List/Dict) │
│  • 选择合适的可视化方式                │
│  • 生成完整 HTML 页面                  │
└───────────────────────────────────────┘
        ↓
    保存到沙盒 → SSE Artifact → 前端展示
```

**核心优势：**

- 🎨 **完全自由** - LLM 自主选择图表类型、配色、布局
- � **多种形式** - 支持图表、表格、信息图、Dashboard
- 🌐 **原生 HTML** - 内联 CSS/JS，无外部依赖
- � **即时交付** - 通过 SSE Artifact 实时推送给前端

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    L1 展现层 (Presentation)                      │
│            React 19 + Vite + Shadcn/UI + Zustand                │
│    ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│    │ 对话界面  │ │ 知识库   │ │ 数据源   │ │ 权限管理  │          │
│    └──────────┘ └──────────┘ └──────────┘ └──────────┘          │
└─────────────────────────┬───────────────────────────────────────┘
                          │ SSE 双轨事件流 (Telemetry + Conversation)
┌─────────────────────────┴───────────────────────────────────────┐
│                    L2 网关层 (Gateway)                           │
│                 FastAPI + Pydantic + SSE-Starlette              │
│          JWT Auth · CORS · Rate Limiting · Request Validation   │
└─────────────────────────┬───────────────────────────────────────┘
                          │
┌─────────────────────────┴───────────────────────────────────────┐
│                  L3 编排层 (Orchestration)                       │
│              LangGraph Supervisor-Worker 智能体网络              │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                      Supervisor                             │ │
│  │   ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐   │ │
│  │   │ Planner │→ │ Executor │→ │ Reflector │→ │Synthesizer│  │ │
│  │   └─────────┘  └────┬─────┘  └───────────┘  └──────────┘   │ │
│  │                     ↓                                       │ │
│  │   ┌─────────────────┴─────────────────────────────────────┐│ │
│  │   │                    Workers                            ││ │
│  │   │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  ││ │
│  │   │  │SqlWorker │ │DocWorker │ │OfficeWork│ │ Analyst  │  ││ │
│  │   │  │Text2SQL  │ │ RAG问答  │ │文件处理   │ │ 数据分析 │  ││ │
│  │   │  └──────────┘ └──────────┘ └──────────┘ └──────────┘  ││ │
│  │   └───────────────────────────────────────────────────────┘│ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────┬───────────────────────────────────────┘
                          │
┌─────────────────────────┴───────────────────────────────────────┐
│                   L4 能力层 (Capabilities)                       │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌────────────┐ │
│  │ SchemaSkill │ │  SqlSkill   │ │  DocSkill   │ │ PythonSkill│ │
│  │ 元数据检索   │ │ SQL执行验证  │ │ 文档RAG检索  │ │ Python沙盒 │ │
│  └─────────────┘ └─────────────┘ └─────────────┘ └────────────┘ │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ ChromaDB │  │   MySQL  │  │  Redis   │  │ 通义千问 / 析言GBI│ │
│  │ 向量存储  │  │ 业务数据  │  │ VLM缓存  │  │   LLM / VLM     │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 智能体协作流程 (LangGraph 状态机)

系统采用 **环状图** 设计，支持反思重试、用户确认、动态路由等复杂流程：

```
                          ┌──────────────────────────────────────────┐
                          │                                          │
                          ▼                                          │
用户提问 → ┌─────────────────┐    ┌─────────────────┐                │
          │ Intent Classifier│───→│     Planner     │                │
          │   意图分类器      │    │   任务规划器    │                │
          └─────────────────┘    └────────┬────────┘                │
                                          │                          │
                    ┌─────────────────────┼─────────────────────┐    │
                    ▼                     ▼                     ▼    │
             ┌────────────┐       ┌────────────┐        ┌────────┐  │
             │Human Review│       │   Suspend  │        │Executor│  │
             │ 用户确认   │       │  挂起等待   │        │ 执行器 │  │
             └─────┬──────┘       └────────────┘        └───┬────┘  │
                   │                                        │       │
                   └────────────────────┬───────────────────┘       │
                                        ▼                           │
                                 ┌────────────┐                     │
                                 │  Reflector │ ─── fail ───────────┘
                                 │   反思器   │          (重试回环)
                                 └─────┬──────┘
                                       │ pass
                                       ▼
                                ┌─────────────┐    ┌───────────┐
                                │ Synthesizer │───→│ Summarizer│───→ 输出
                                │   综合器    │    │  摘要器   │
                                └─────────────┘    └───────────┘
```

### ⚙️ 用户可配置功能

系统支持**按工作区自定义**的智能体行为配置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| **max_retries** | 最大重试次数 (0=禁用反思重试) | 2 |
| **always_confirm** | 始终需要用户确认计划 | false |
| **自定义 Prompt** | 支持修改 Planner/Reflector/Synthesizer 提示词 | - |

**配置示例：**

```json
{
  "max_retries": 3,
  "always_confirm": true
}
```

**节点路由逻辑：**

| 场景 | 路由 |
|------|------|
| 闲聊/直接回答 | Intent → Synthesizer (跳过 Planner) |
| 需确认计划 | Planner → Human Review → Executor |
| 自动确认 | Planner → Executor (跳过 Human Review) |
| 反思失败 | Reflector → Planner (重新规划) |
| 反思通过 | Reflector → Synthesizer |
| 熔断 | 达到 max_retries → Synthesizer (强制输出) |


### 🔍 Reflector 反思机制详解

Reflector 是系统的质量保障核心，采用**三维判定 + 分流策略 + 熔断保护**机制：

#### 三维判定

| 维度 | 检查内容 | 失败处理 |
|------|----------|----------|
| 🔧 **语法 (Syntax)** | SQL 语法错误、执行异常、代码报错 | 重新规划 SQL |
| ✅ **有效性 (Validity)** | 空结果、无输出文件、数据缺失 | 放宽条件重试 |
| 🎯 **相关性 (Relevance)** | 结果与问题不匹配、答非所问 | 重新理解问题 |
| 🔐 **权限 (Permission)** | 无权访问数据表 | 直接熔断，不重试 |

#### 分流策略

```
执行结果
    ↓
┌───────────────────────────────────────┐
│  判断任务类型                          │
│  • Extraction: sql_worker, doc_worker │
│  • Creation: office_worker, chart     │
└───────────────┬───────────────────────┘
                ↓
        ┌───────┴───────┐
        ↓               ↓
┌───────────────┐ ┌───────────────┐
│ Extraction    │ │ Creation      │
│ Quick + LLM   │ │ Quick Only    │
│ 完整三维检查  │ │ 仅技术检查    │
└───────────────┘ └───────────────┘
```

- **Extraction 任务** (SQL/RAG)：执行 Quick Check + LLM Check 完整校验
- **Creation 任务** (文件生成/图表)：仅执行 Quick Check，跳过 LLM

#### 熔断保护

| 机制 | 配置 | 行为 |
|------|------|------|
| 最大重试 | `SUPERVISOR_MAX_RETRIES=2` | 超过次数强制终止 |
| 权限错误 | - | 立即熔断，不浪费资源 |
| 工具回退 | `RAG_KEYWORDS` | SQL 失败时建议切换到 RAG |

#### 判定结果

| Verdict | 含义 | 后续动作 |
|---------|------|----------|
| `pass` | 审核通过 | 进入 Synthesizer |
| `partial` | 部分完成 | 继续执行剩余步骤 |
| `fail` | 审核失败 | 触发 Planner 重规划 |
| `break` | 熔断终止 | 直接输出错误信息 |

---

## 🚀 快速开始

### 环境要求

| 组件 | 版本要求 |
|------|----------|
| Python | 3.10+ |
| Node.js | 18+ |
| MySQL | 8.0+ |
| Redis | 5.0+ (可选，用于 VLM 缓存) |

### 1️⃣ 克隆仓库

```bash
git clone https://github.com/your-org/DeluData.git
cd DeluData
```

### 2️⃣ 后端启动

```bash
cd backend

# 创建虚拟环境
python -m venv venv

# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量（复制并编辑）
cp .env.example .env

# 启动服务
uvicorn app.entrypoints.admin_main:app --reload --host 0.0.0.0 --port 8000
uvicorn app.entrypoints.experience_main:app --reload --host 0.0.0.0 --port 8001
```

> 💡 **Windows 用户**：可直接双击 `启动后端.bat` 一键启动

### 3️⃣ 前端启动

```bash
cd frontend

# 安装依赖
npm install

# 开发模式启动
npm run dev
```

### 4️⃣ 访问服务

| 服务 | 地址 |
|------|------|
| 🌐 前端界面 | http://localhost:5173 |
| 📖 API 文档 | http://localhost:8000/docs |
| ❤️ 健康检查 | http://localhost:8000/health |

---

## ⚙️ 配置说明

### 后端配置 (`backend/.env`)

```env
# ============ 数据库配置 ============
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=DeluData

# ============ LLM 配置 ============
DASHSCOPE_API_KEY=your_dashscope_api_key

# ============ 各组件模型 ============
PLANNER_MODEL=qwen-plus          # 规划器模型
REFLECTOR_MODEL=qwen-plus        # 反思器模型
SYNTHESIZER_MODEL=qwen-plus      # 综合器模型
SQL_WORKER_MODEL=qwen-plus       # SQL Worker 模型
OFFICE_WORKER_MODEL=qwen-plus    # 文件处理模型
DOC_WORKER_MODEL=qwen-plus       # RAG 问答模型
FAST_MODEL=qwen-turbo            # 快速模型（意图分类等）

# ============ 向量数据库 ============
CHROMA_PERSIST_DIR=./data/chroma

# ============ RAG 配置 ============
RAG_EMBEDDING_MODEL=text-embedding-v3
RAG_RERANKER_MODEL=qwen3-rerank
RAG_RERANK_TOP_K=5

# ============ Redis (可选) ============
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
```

### 前端配置 (`frontend/src/config.ts`)

```typescript
export const API_BASE_URL = 'http://localhost:8000';
```

---

## 📁 项目结构

```
DeluData/
├── backend/                    # 后端服务
│   ├── app/
│   │   ├── api/                # API 路由层
│   │   │   ├── auth/           # 认证相关
│   │   │   ├── chat/           # 对话接口
│   │   │   ├── knowledge/      # 知识库管理
│   │   │   │   ├── documents.py    # 文档上传/管理
│   │   │   │   ├── folders.py      # 文件夹管理
│   │   │   │   └── images.py       # 图片服务
│   │   │   ├── files/          # 文件服务
│   │   │   └── events.py       # SSE 事件流
│   │   ├── agents/             # Worker 智能体
│   │   │   ├── sql_worker.py       # SQL 问答
│   │   │   ├── office_worker.py    # 文件处理
│   │   │   ├── office/             # 文件处理子模块
│   │   │   │   ├── handlers/       # 策略处理器
│   │   │   │   ├── file_detector.py    # 文件类型检测
│   │   │   │   └── error_translator.py # 友好错误翻译
│   │   │   └── reflector/      # 反思机制
│   │   ├── supervisor/         # Supervisor 编排
│   │   │   ├── graph.py        # LangGraph 图定义
│   │   │   ├── nodes/          # 节点实现
│   │   │   │   ├── planner/    # 规划器
│   │   │   │   ├── executor/   # 执行器
│   │   │   │   ├── reflector/  # 反思器
│   │   │   │   └── synthesizer/# 综合器
│   │   │   ├── edges.py        # 路由逻辑
│   │   │   └── state.py        # 共享状态
│   │   ├── skills/             # L4 能力技能
│   │   │   ├── schema_skill.py # 元数据检索
│   │   │   ├── sql_skill.py    # SQL 执行
│   │   │   ├── doc_skill.py    # RAG 文档检索
│   │   │   ├── python_skill.py # Python 沙盒
│   │   │   └── xiyan_skill.py  # 析言 GBI 集成
│   │   ├── core/               # 核心模块
│   │   │   ├── db/             # 数据库连接
│   │   │   ├── rag/            # RAG 组件
│   │   │   │   ├── semantic_chunker.py # 语义切分
│   │   │   │   ├── hybrid_retriever.py # 混合检索
│   │   │   │   ├── reranker.py        # 重排序
│   │   │   │   ├── context_expander.py # 上下文扩展
│   │   │   │   └── query_rewriter.py  # 查询改写
│   │   │   ├── llm/            # LLM 客户端
│   │   │   │   ├── async_llm.py       # 异步 LLM
│   │   │   │   ├── async_embedding.py # 异步 Embedding
│   │   │   │   └── vlm_service.py     # VLM 视觉服务
│   │   │   └── utils/          # 工具类
│   │   │       ├── image_service.py   # 图片服务
│   │   │       └── sandbox_cleanup.py # 沙盒清理
│   │   ├── services/           # 业务服务
│   │   │   └── ingestion_service.py   # 文档解析服务
│   │   ├── models/             # Pydantic 数据模型
│   │   ├── config.py           # 配置管理
│   │   └── main.py             # FastAPI 入口
│   ├── prompts/                # Prompt 模板 (YAML)
│   │   ├── planner.yaml        # 规划器提示词
│   │   ├── reflector.yaml      # 反思器提示词
│   │   └── supervisor.yaml     # 综合器提示词
│   ├── migrations/             # 数据库迁移
│   └── requirements.txt        # Python 依赖
│
├── frontend/                   # 前端应用
│   ├── src/
│   │   ├── components/         # UI 组件
│   │   │   ├── chat/           # 对话相关
│   │   │   │   ├── ChatMessage.tsx    # 消息组件
│   │   │   │   ├── ChatInput.tsx      # 输入框
│   │   │   │   └── FloatingTaskPanel.tsx # 任务面板
│   │   │   └── knowledge/      # 知识库相关
│   │   │       ├── PdfViewer.tsx      # PDF 预览
│   │   │       └── FilePreview.tsx    # 文件预览
│   │   ├── pages/              # 页面
│   │   │   ├── ChatPage.tsx        # 主对话页
│   │   │   ├── KnowledgeBasePage.tsx   # 知识库管理
│   │   │   ├── DatabaseConfigPage.tsx  # 数据源配置
│   │   │   ├── UserManagePage.tsx      # 用户管理
│   │   │   ├── RoleManagePage.tsx      # 角色管理
│   │   │   └── PermissionsPage.tsx     # 权限管理
│   │   ├── hooks/              # React Hooks
│   │   │   ├── useChatSSE.ts       # SSE 连接
│   │   │   └── useTaskPlanner.ts   # 任务规划状态
│   │   ├── stores/             # Zustand 状态管理
│   │   │   └── chatStore.ts    # 对话状态
│   │   ├── api/                # API 客户端
│   │   └── types/              # TypeScript 类型
│   ├── package.json
│   └── vite.config.ts
│
└── start_app.bat               # 一键启动脚本
```

---

## 🔐 安全设计

### 双重围栏机制

```
用户问题
    ↓
┌─────────────────────────────────────┐
│  🛡️ 语义防火墙 (SchemaSkill)         │
│  • 检索时强制 workspace_id 过滤      │
│  • 只返回用户有权限的表结构          │
└─────────────────┬───────────────────┘
                  ↓
┌─────────────────────────────────────┐
│  🛡️ 执行防火墙 (SqlSkill)            │
│  • SQL AST 解析提取表名             │
│  • 校验是否在权限白名单内           │
│  • 拦截 DROP/TRUNCATE 等危险操作    │
└─────────────────┬───────────────────┘
                  ↓
              安全执行
```

### RBAC 权限模型

- **组织 (Organization)** → **部门 (Department)** → **用户 (User)**
- **角色 (Role)** ↔ **权限 (Permission)**
- 支持数据源级别、表级别的精细权限控制

### 文件权限模型

| 可见性 | 描述 |
|--------|------|
| `public` | 全员可见 |
| `dept` | 部门可见 |
| `private` | 仅所有者可见 |

---

## 📊 SSE 双轨事件流

系统使用 Server-Sent Events 实现实时通信，分为两个频道：

| 频道 | 用途 | 示例事件 |
|------|------|----------|
| `conversation` | 对话内容 | `MESSAGE_CHUNK`, `MESSAGE_END` |
| `telemetry` | 执行状态 | `STEP_UPDATE`, `PLAN_COMPLETE`, `FILE_RESULT` |

```javascript
// 事件格式示例
{
  "channel": "telemetry",
  "type": "STEP_UPDATE",
  "payload": {
    "step_id": "step_01",
    "status": "running",
    "label": "正在分析数据表结构..."
  }
}
```

### 主要事件类型

| 事件 | 描述 |
|------|------|
| `PLAN_COMPLETE` | 任务规划完成 |
| `STEP_UPDATE` | 步骤状态更新 |
| `MESSAGE_CHUNK` | 流式消息片段 |
| `MESSAGE_END` | 消息结束 |
| `FILE_RESULT` | 文件生成完成 |
| `CHART_RESULT` | 图表生成完成 |

---

## 🛠️ 技术栈

### 后端

| 技术 | 版本 | 用途 |
|------|------|------|
| FastAPI | 0.115+ | Web 框架 |
| LangGraph | 0.2+ | 智能体编排 |
| LangChain | 0.3+ | LLM 工具链 |
| ChromaDB | 0.5+ | 向量数据库 |
| SQLAlchemy | 2.0+ | ORM |
| PyMuPDF | 1.24+ | PDF 解析 |
| python-docx | 1.1+ | Word 处理 |
| openpyxl | 3.1+ | Excel 处理 |
| 通义千问 | - | LLM/VLM |
| BM25s | 0.2+ | 稀疏检索 |

### 前端

| 技术 | 版本 | 用途 |
|------|------|------|
| React | 19+ | UI 框架 |
| Vite | 7+ | 构建工具 |
| TypeScript | 5.9+ | 类型安全 |
| Zustand | 5+ | 状态管理 |
| Shadcn/UI | - | 组件库 |
| ECharts | 6+ | 图表可视化 |
| TailwindCSS | 4+ | 样式框架 |
| react-pdf | 10+ | PDF 预览 |

---

## 📝 开发指南

### 添加新的 Worker 智能体

1. 在 `app/agents/` 创建新的 Worker 文件
2. 继承 `BaseWorker` 或实现标准接口
3. 在 `worker_categories.py` 中注册
4. 在 Supervisor 的 Planner Prompt 中添加描述

### 添加新的 Skill 能力

```python
from app.skills.base import BaseSkill

class MyNewSkill(BaseSkill):
    def __init__(self):
        super().__init__(name="MyNewSkill")
    
    async def execute(self, *args, **kwargs):
        """实现技能逻辑"""
        pass
```

### Prompt 管理

所有 Prompt 模板存放在 `backend/prompts/` 目录，使用 YAML 格式：

```yaml
# prompts/planner.yaml
system: |
  你是一个专业的任务规划师...
  
user: |
  用户问题：{question}
```

### 工程规范

1. **异步优先** - 所有 IO 操作必须使用 `async/await`
2. **配置外置** - 禁止硬编码，使用 `config.py` 或环境变量
3. **类型安全** - 使用 Pydantic 进行数据校验
4. **零技术债** - 及时清理未使用的代码

---

## 🧪 测试

```bash
# 后端单元测试
cd backend
pytest tests/ -v

# 前端测试
cd frontend
npm run test
```

---

## 📄 License

本项目采用 [MIT License](LICENSE) 开源协议。

---

## 🤝 贡献指南

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 提交 Pull Request

---

<p align="center">
  <strong>DeluData</strong> - 让数据会说话 💬。
</p>
