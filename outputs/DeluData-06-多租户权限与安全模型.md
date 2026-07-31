---
title: DeluData 多租户权限与安全模型
type: security-notes
tags:
  - 工作/项目
  - 技术/安全
  - 技术/后端
created: 2026-07-16
updated: 2026-07-16
status: active
---

# DeluData：多租户权限与安全模型

返回：[[DeluData-项目知识索引]]

## 第一原则

`workspace_id` 是安全边界，不是普通查询条件。认证、组织、数据库、文档、文件、Checkpoint、Kiosk 设备和后台任务都必须绑定 Workspace。

## 普通用户认证链

```text
Bearer JWT
  → decode_token
  → 加载用户并检查 disabled
  → 检查 Workspace active
  → set_current_workspace
  → build_effective_access_context
  → UserContext
```

`UserContext` 向 Supervisor 和 Worker提供：用户 ID、Workspace、角色、Capability、部门、数据范围和允许表。前端传入的上下文只能收窄权限，不能扩权。

## Capability Scope

Authorization V2 不只判断“有没有权限”，还计算：

- `capabilities`：可执行的能力代码。
- `capability_scopes`：能力允许作用在哪些对象。
- `denied_capability_scopes`：显式拒绝范围。
- `authorization_revision`：授权版本，便于缓存与失效。
- 组织单元 Assignment：用户与部门、岗位等组织关系。

平台管理员与 Workspace 管理员是不同身份边界。平台 Token 存储在 `platform_token`，平台 API 使用 `/api/platform`。

## 数据层隔离

- SQL：Workspace 数据源、白名单、语义策略、允许表与只读执行器。
- 文档：Workspace、部门、文件可见性、软删除与 Doc Scope。
- 文件：路径安全、沙箱目录、Artifact ID 和下载权限。
- LangGraph：`thread_id=session_id`，配置中携带用户 ID，避免跨用户读取 Checkpoint。
- 后台队列：任务记录携带 Workspace 和 Run Token，领取时限制并发。

## Kiosk 设备鉴权

Kiosk 不使用普通登录 Token：

1. 激活码绑定 Workspace 并创建设备。
2. 客户端保存 `kiosk_device_token`。
3. 每个请求通过 `X-Device-Token` 发送。
4. 后端检查设备、Workspace 状态和 `kiosk_enabled`。
5. 根据设备的 Service User 和部门生成 Experience UserContext。
6. 会话查询同时限定 Workspace 与 Device ID。

Debug 模式可自动注册符合前缀的开发设备，生产环境不能依赖此路径。

## Feature Flag

Workspace 上的 `museum_enabled`、`kiosk_enabled` 同时受前后端保护：前端 Guard 控制入口，后端 Dependency 才是最终安全边界。不能仅靠隐藏菜单禁用功能。

## 文件与路径安全

- 所有生成产物进入会话沙箱或受控存储。
- 不接受未经规范化的任意绝对路径。
- MCP 的 `local_path` 默认关闭，并受 Allowed Directories 限制。
- 文件删除需要软删除过滤和物理清理策略配合。
- 不把密钥、数据库密码、设备 Token 写入源码或日志。

## 安全修改方法

1. 先画出调用者、Workspace、主体、Capability、对象范围和拒绝规则。
2. 在 Service 层构造有效访问上下文，在查询层再次限定 Workspace。
3. 前端只负责体验，后端必须独立拒绝越权请求。
4. 增加跨租户、禁用用户、禁用 Workspace、显式拒绝和软删除测试。
5. 检查后台 Worker 与 MCP 是否复用了同一权限边界。

## 重点测试

- `test_authorization_v2.py`
- `test_authorization_v2_migration_mysql.py`
- `tests/integration/test_tenant_security.py`
- `test_data_scope_helpers.py`
- `test_chat_context_security.py`
- `test_semantic_access_policy_contract.py`
- `test_filesystem_soft_delete_filters.py`
- `test_science_experience_services.py`

相关：[[DeluData-04-Text-to-SQL与可信语义治理]]、[[DeluData-07-前端产品形态与接口契约]]
