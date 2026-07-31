# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DeluData is an enterprise AI-powered data Q&A and intelligent analysis platform. It uses a LangGraph-based multi-agent architecture (Supervisor-Worker pattern) to handle natural language queries, converting them to SQL, performing RAG-based document retrieval, and generating reports/visualizations.

## Development Commands

### Backend (FastAPI + Python)
```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Linux/Mac
pip install -r requirements.txt
uvicorn app.entrypoints.admin_main:app --reload --host 0.0.0.0 --port 8000
uvicorn app.entrypoints.experience_main:app --reload --host 0.0.0.0 --port 8001
```

### Frontend (React + Vite)
```bash
cd frontend
npm install
npm run dev      # Development server
npm run build    # Build: tsc -b && vite build
npm run lint     # ESLint
```

### Testing
```bash
# Backend
cd backend && pytest tests/ -v

# Frontend
cd frontend && npm run test
```

### Quick Start (Windows)
Double-click `start_app.bat` to launch both frontend and backend simultaneously.

## Architecture

### E-SOA Layered Architecture
- **L1 Presentation**: React 19 + Vite + Shadcn/UI + Zustand
- **L2 Gateway**: FastAPI + Pydantic + SSE-Starlette (JWT Auth, CORS, Rate Limiting)
- **L3 Orchestration**: LangGraph Supervisor-Worker agent network
- **L4 Capabilities**: Skills layer (SchemaSkill, SqlSkill, DocSkill, PythonSkill)

### LangGraph Agent Flow
```
User → Intent Classifier → Planner → [Human Review] → Executor → Reflector → Synthesizer → Output
                                           ↓
                              Workers: SqlWorker, DocWorker, OfficeWorker, Analyst
```

Key nodes:
- **Planner**: Task decomposition and worker assignment
- **Executor**: Dispatches tasks to specialized workers
- **Reflector**: Quality validation with 3-dimensional checks (Syntax, Validity, Relevance) + circuit breaker
- **Synthesizer**: Final response composition

### Backend Structure (`backend/app/`)
- `supervisor/` - LangGraph graph definition, nodes (planner, executor, reflector, synthesizer), edges, state
- `agents/` - Worker implementations (sql_worker, office_worker, doc_worker)
- `skills/` - L4 capabilities (schema_skill, sql_skill, doc_skill, python_skill)
- `core/` - Database, RAG components, LLM clients, utilities
- `api/` - FastAPI routes (auth, chat, knowledge, files, events)
- `services/` - Business logic (ingestion_service for document parsing)
- `models/` - Pydantic data models

### Frontend Structure (`frontend/src/`)
- `pages/` - ChatPage, KnowledgeBasePage, DatabaseConfigPage, UserManagePage
- `components/` - UI components (chat/, knowledge/)
- `hooks/` - useChatSSE (SSE connection), useTaskPlanner
- `stores/` - Zustand state management (chatStore)
- `api/` - API client functions

### Prompt Templates
All prompts are in `backend/prompts/` using YAML format:
```yaml
system: |
  Your system prompt here...
user: |
  User question: {question}
```

## Key Patterns

### SSE Dual-Channel Events
Two channels for real-time communication:
- `conversation`: MESSAGE_CHUNK, MESSAGE_END
- `telemetry`: STEP_UPDATE, PLAN_COMPLETE, FILE_RESULT, CHART_RESULT

### Security: Dual Firewall
1. **Semantic Firewall** (SchemaSkill): Filters schema by workspace_id permissions
2. **Execution Firewall** (SqlSkill): SQL AST parsing, table whitelist validation, blocks dangerous operations

### Adding New Components
- **New Worker**: Create in `app/agents/`, register in `worker_categories.py`, add to Planner prompt
- **New Skill**: Inherit from `BaseSkill` in `app/skills/`

## Configuration

### Backend (`backend/.env`)
Key variables: `DB_HOST`, `DB_PORT`, `DASHSCOPE_API_KEY`, model configs (`PLANNER_MODEL`, `SQL_WORKER_MODEL`, etc.), `CHROMA_PERSIST_DIR`, RAG settings

### Frontend (`frontend/src/config.ts`)
API base URL configuration

## Engineering Conventions
- Async-first: All IO operations use `async/await`
- Config externalization: Use `config.py` or environment variables, no hardcoding
- Type safety: Pydantic for data validation
