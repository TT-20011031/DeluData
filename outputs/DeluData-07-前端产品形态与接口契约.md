---
title: DeluData 前端产品形态与接口契约
type: frontend-notes
tags:
  - 工作/项目
  - 技术/前端
  - 技术/API
created: 2026-07-16
updated: 2026-07-16
status: active
---

# DeluData：前端产品形态与接口契约

返回：[[DeluData-项目知识索引]]

## 租户主工作台

目录 `frontend/`，技术栈 React 19、Vite、TypeScript、Zustand、Radix/Shadcn 风格组件。

主要路由：

- `/`、`/chat/:sessionId`：聊天工作台。
- `/knowledge`：知识库。
- `/database`：数据库配置。
- `/database/semantic-governance`：语义治理和访问策略。
- `/admin/authorization`：统一授权中心。
- `/extend-config`：模板、Skill 等扩展配置。
- `/agent-config`：用户 Agent 配置。
- `/admin/science`：Kiosk 管理。
- `/museum/*`：博物馆扩展场景。

旧用户、角色、部门路由会重定向到 Authorization Center，说明当前 UI 正在向统一授权模型收敛。

## 聊天前端链路

```text
ChatPage
  → useChatActions
  → 先 connect SSE
  → chatService.startChat
  → useTaskPlanner 管理计划
  → useChatSSE 分发事件
  → useChatSSEHandlers 更新 Zustand
```

核心 API：

- `POST /api/chat/start`：创建/继续会话并生成计划。
- `POST /api/chat/confirm`：确认或修改计划后执行。
- `POST /api/chat/resume`：补充信息并恢复挂起任务。
- `GET /api/chat/sessions/{id}/plan`：刷新后恢复计划。
- `GET /api/chat/sessions/{id}/history`：恢复消息。

`/api/chat/send` 已废弃，不能作为新功能入口。

## 前端状态分工

- `chatStore`：会话、消息、流式正文、推理内容和 Artifact。
- `authStore`：Token、用户和 Workspace Feature。
- `missionStore`：任务执行状态。
- `artifactBoxStore`：产物预览与管理。
- `dbStore`、`uploadStore`、`layoutStore`：数据库、上传和布局。

前端包含消息去重、乐观用户消息、刷新恢复和直连模式兜底，修改流式处理时要避免重复消息与卡住的 Thinking 占位符。

## SSE 契约

`SSEClient` 连接 `/api/events/stream/{sessionId}`，按事件名注册监听。高频 `reasoning_chunk` 使用 `requestAnimationFrame` 缓冲，减少 Store 更新次数。

任何新增事件需要同步：后端 EventType、发送函数、前端 `SSE_EVENTS`、`useChatSSE`、Handler、Store 和 UI。

## 平台管理端

目录 `frontend-platform/`。使用 BrowserRouter 和 `/platform/` basename，Axios 基础路径为 `/api/platform`。主要页面：Dashboard、Tenants、Knowledge Governance、Admins、Settings、DB Whitelist。

## Kiosk 体验端

目录 `kiosk-frontend/`。设备 Token 缺失时进入 Activation；有 Token 后进入 Experience Shell。

页面状态：Activation → Attract → PersonaIntro → Listening → Thinking → Answer；Quiz 与 Reward 是另一条分支。

API 使用 `X-Device-Token`，主要包括设备激活、Session、画像推断、ASR、问答、答题和优惠券二维码。Zustand 保存访客画像、当前问答、TTS、检索来源和答题进度。

## 前端开发原则

1. 复用现有 Store、Service、Hook 和组件，不引入另一套状态范式。
2. 路由 Guard 只改善体验，权限仍由后端保证。
3. 修改 API Schema 时同步 TypeScript 类型和恢复逻辑。
4. 流式事件必须考虑乱序、重连、重复和页面刷新。
5. 终结类任务要同时更新任务面板、消息区和 Artifact 预览。
6. 主前端构建前会同步 PDF Worker、cmaps 和字体资源。

相关：[[DeluData-03-LangGraph-Supervisor执行方法]]、[[DeluData-06-多租户权限与安全模型]]
