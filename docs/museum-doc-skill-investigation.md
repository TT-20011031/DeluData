# 博物馆导览知识库检索问题调查报告

## 问题描述

博物馆导览界面 (museum) 的回复没有经过知识库检索 (doc_skill)，导致 AI 回复缺乏具体展品信息。

---

## 调查结果

### 根本原因：`workspace_id` 配置不一致

**问题定位：** `@backend/app/museum/services/guide_service.py:299`

在 `_search_exhibits` 方法中，构建 `UserContext` 时 `workspace_id` 被硬编码为 `"museum"`：

```python
# 原代码 (有问题)
user_context = UserContext(
    user_id="museum_visitor",
    workspace_id="museum",  # ❌ 硬编码为 "museum"
    ...
)
```

而文档上传时使用的 `workspace_id` 默认为 `"default"`（见 `@backend/app/models/common/context.py:30`）：

```python
class UserContext(BaseModel):
    workspace_id: str = Field(default="default", ...)
```

**结果：** 检索时查询 `workspace_id="museum"`，但文档存储在 `workspace_id="default"` 下，导致检索结果为空。

---

### 检索流程追踪

```
前端 GuidePage
    ↓ 语音/文字输入
useGuideSSE.startGuide() / continueChat()
    ↓ POST /museum/events/guide/stream
events.py → sse_generator()
    ↓
guide_service.py → process_guide()
    ↓ Phase 1: 并行预加载
guide_service.py → _search_exhibits(query, dept_id)
    ↓
doc_skill.py → query_knowledge_base(query, user_context)
    ↓
hybrid_retriever.py → search(queries, user_context)
    ↓ 构建权限过滤条件
workspace_filter = {"workspace_id": {"$eq": user_context.workspace_id}}
    ↓
ChromaDB.query(where=workspace_filter)
    ↓
❌ 返回空结果（workspace_id 不匹配）
```

---

### 部门设置功能（SettingsPanel）

**功能正常**，但 `deptId` 的作用是权限过滤，不影响 `workspace_id` 问题：

- 前端 `SettingsPanel` 组件可正确获取部门列表
- 选择部门后 `deptId` 会通过 `guideStore` 保存到 `localStorage`
- 请求时 `deptId` 会传递给后端用于知识库权限过滤
- 但如果 `workspace_id` 不匹配，即使 `deptId` 正确也无法检索到文档

---

### 多模态查询（图片检索）

**存在相同问题**。多模态查询走相同的 `_search_exhibits` 方法，同样受 `workspace_id` 不匹配影响：

```python
# guide_service.py Phase 1
search_task = asyncio.create_task(
    self._search_exhibits(query, dept_id)  # 同一方法
)
```

---

## 修复方案

### 修复 1：使用配置的 workspace_id

**文件：** `@backend/app/museum/services/guide_service.py`

```python
# 修复后代码
settings = get_museum_settings()
user_context = UserContext(
    user_id="museum_visitor",
    workspace_id=settings.guide_workspace_id,  # ✅ 使用配置值
    ...
)
```

**配置项：** `@backend/app/museum/config.py`

```python
class MuseumSettings(BaseSettings):
    guide_workspace_id: str = "default"  # 与文档上传的 workspace_id 一致
```

**环境变量配置：**
```env
MUSEUM_GUIDE_WORKSPACE_ID=default
```

---

## 新增功能：文字输入（调试用）

为便于调试，新增了文字输入组件，与语音输入走相同路径：

**新增文件：** `@frontend/src/museum/components/TextInput.tsx`

**使用方式：** 在导览页面底部输入区新增"文字"按钮，点击展开输入框，回车发送。

---

## 验证步骤

1. 确认 `.env` 中 `MUSEUM_GUIDE_WORKSPACE_ID=default`（或与文档上传时一致的值）
2. 确认知识库中有文档（可通过管理后台上传）
3. 使用文字输入或语音输入提问，观察控制台日志：
   ```
   [GuideService] 知识库检索: workspace_id=default, query=xxx
   [DocSkill] 检索到 N 条结果
   ```
4. 检查 SEARCH_RESULT SSE 事件的 `count` 字段是否 > 0

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `backend/app/museum/services/guide_service.py` | 导览主服务，`_search_exhibits` 方法 |
| `backend/app/museum/config.py` | 博物馆配置，`guide_workspace_id` |
| `backend/app/skills/doc_skill.py` | 知识库检索技能 |
| `backend/app/core/rag/hybrid_retriever.py` | 混合检索器，权限过滤逻辑 |
| `frontend/src/museum/pages/GuidePage.tsx` | 导览页面 |
| `frontend/src/museum/components/TextInput.tsx` | 新增文字输入组件 |
| `frontend/src/museum/components/SettingsPanel.tsx` | 部门设置面板 |

---

## 总结

| 问题 | 原因 | 状态 |
|------|------|------|
| 导览不经过知识库检索 | `workspace_id` 硬编码为 "museum"，与文档的 "default" 不匹配 | ✅ 已修复 |
| **检索返回 0 结果** | `rerank_score_threshold=0.3` 过高，实际分数约 0.01-0.03 | ✅ 已修复 (降为 0.01) |
| 图片不显示 | `_extract_images` 读取错误字段 `image_ids`，应为 `related_image_ids` | ✅ 已修复 |
| `DocumentChunk` 属性错误 | 方法使用 `.get()` 访问 dataclass 属性 | ✅ 已修复 |
| 部门设置功能 | 功能正常，但受上述问题影响无效果 | ✅ 修复后正常 |
| 多模态检索 | 走相同路径，存在相同问题 | ✅ 已修复 |
| 文字输入调试 | 原本只有语音输入 | ✅ 已新增 |

---

## 补充修复（2026-01-26）

### 问题 2：检索分数阈值过高

**现象：** 即使有匹配文档，`doc_skill.query_knowledge_base()` 返回空结果

**根因：** `@backend/app/config.py:186`
```python
rerank_score_threshold: float = 0.3  # 原值过高
```

实际 rerank 分数约 0.01-0.03，0.3 阈值过滤掉了所有结果。

**修复：**
```python
rerank_score_threshold: float = 0.01  # 降低阈值
```

### 问题 3：图片字段名不匹配

**现象：** 检索有结果但图片不显示

**根因：** `guide_service._extract_images()` 读取 `image_ids` 字段，但实际存储字段名为：
- `related_image_ids` (图文语义关联)
- `linked_image_ids` ([IMAGE:ID] 精确标记)

**修复：** 已更新 `_extract_images()` 读取正确字段名

---

## 诊断脚本

新增以下诊断脚本供后续排查：

| 脚本 | 用途 |
|------|------|
| `scripts/diagnose_museum_images.py` | 诊断文档图片提取和关联 |
| `scripts/search_test.py` | 测试检索并查看图片关联 |
| `scripts/debug_docskill.py` | 调试 DocSkill 完整流程 |
| `scripts/direct_chroma_search.py` | 直接 ChromaDB 查询 |
| `scripts/check_images_dir.py` | 检查图片存储目录 |
