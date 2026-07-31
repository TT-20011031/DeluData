# PDF 多模态 RAG 实施计划书

> 基于 NotebookLM 架构调研 + 实际 PDF 提取测试验证
> 核心思路：**文本 OCR 粗筛定位页码 → 页面图片 + 多模态 VLM 精读回答**

---

## 一、背景与问题

### 1.1 实测验证的关键发现

使用《机械设计手册（第五版）第1卷》（99页，8MB，扫描版 PDF）进行测试：

| 发现 | 详情 |
|------|------|
| **PDF 是纯扫描版** | 每页只有一张扫描图片，无文本层。PyMuPDF `get_text()` 返回空字符串 |
| **当前系统完全失效** | DocumentParser 提取 0 字符，ChromaDB 无任何内容，检索不到任何信息 |
| **OCR 可以提取文字** | RapidOCR @300dpi 分块策略可识别中文标题+表格数字，但丢失表格结构 |
| **OCR 速度** | 平均 ~41s/页（含 300dpi 渲染 + 3 块 OCR），99 页约 67 分钟 |
| **分块 OCR 不可省** | 不分块时 RapidOCR 自动缩放大图，丢失全部中文标题（0 行中文 vs 分块后有标题） |
| **横排页面需旋转** | 宽 > 高 ×1.2 时需旋转 90° 再 OCR |
| **水印干扰检测** | PDF 含 "标准分享网 www.bzfxw.com 免费下载" 水印文本层，需过滤后判断 |

### 1.2 当前入库流程（`document_ingestor.py`）

```
1. DocumentParser.parse()           → 提取文本（扫描版 PDF = 空）
2. ImageService.extract_images()    → 提取内嵌图片（非页面渲染）
3. VLMService.describe_image()      → 已禁用
4. SemanticChunker.chunk_document() → 语义切分（空文本 = 0 切片）
5. ImageLinker.link_images()        → 关联图片到切片
6. AsyncEmbedding.embed_texts()     → 向量化
7. ChromaDB.add()                   → 存储
8. SummaryService.submit_chunks()   → 后台摘要
```

**核心缺陷：步骤 1 对扫描版 PDF 返回空，导致后续全部流程无意义。**

---

## 二、目标架构

### 2.1 改造后的入库流程

```
1. DocumentParser.parse()              → 提取文本层（可能为空）
2. [NEW] PageRenderer.render_pages()   → 每页渲染为 PNG @150dpi 存储
3. [NEW] ScannedPageDetector.detect()  → 检测哪些页是扫描版
4. [NEW] OCRService.ocr_pages()        → 对扫描页 OCR 提取文字（并行）
5. [NEW] 文本融合                       → 合并文本层 + OCR 文本，附加 [PAGE:x] 标记
6. SemanticChunker.chunk_document()    → 语义切分（现在有文本了）
7. ImageService.extract_images()       → 提取内嵌图片
8. ImageLinker.link_images()           → 关联图片到切片
9. AsyncEmbedding.embed_texts()        → 向量化
10. ChromaDB.add()                     → 存储
11. SummaryService.submit_chunks()     → 后台摘要
```

### 2.2 改造后的检索-回答流程

```
Query → QueryRewrite → HybridRetriever (Dense+BM25 RRF)
      → Reranker → ContextExpander
      → [NEW] PageImageCollector（收集命中页码的页面图片 + ±1 邻页）
      → [NEW] MultimodalAnswerer（文本 chunks + 页面图片 + 问题 → VLM 回答）
```

---

## 三、现有 RAG 链路分析

### 3.1 切片策略（`semantic_chunker.py`）

```
1. 按 \n\n 和标题行分段
2. 解析 [PAGE:x] 标记，记录每段所属页码
3. 计算段落 embedding
4. 相邻段落余弦相似度 < 0.75 → 语义断点
5. 按断点合并段落为 chunk（100~2000 字符）
6. 建立 prev/next 双向链表
7. 每个 chunk 的 metadata.page_numbers 记录涉及的页码
```

✅ **关键发现：chunk 已有 `page_numbers` 字段！Phase 2 可直接从 reranked chunks 的 metadata 提取页码。**

### 3.2 召回策略（`hybrid_retriever.py`）

```
QueryRewriter (LLM 生成 3 个查询变体)
  → HybridRetriever (Dense向量 Top-30 + BM25关锬词 Top-30, RRF 合并, Top-50)
    → Reranker (qwen3-rerank, Top-15, 阈值 0.12)
      → ContextExpander (前后各 3 个 chunk 链表扩展, 上限 25000 字符)
        → ImageReferenceInjector (注入 [[IMG:...]] 标记)
```

✅ **不需要改动现有检索链路，只需在入库侧改造（提供有内容的文本）和回答侧增强（附加页面图片）。**

### 3.3 OCR 仅针对 PDF 的逎辑

| 文件类型 | 解析方式 | 需要 OCR? |
|----------|----------|----------|
| TXT/MD | 直接读文件 | ✖ |
| DOCX | python-docx 原生提取 | ✖ |
| PDF（文本版） | PyMuPDF get_text() | ✖ |
| PDF（扫描版） | 每页 get_text() 为空→ RapidOCR | ✔ |
| PDF（混合版） | 逻页检测，扫描页走 OCR，文本页用原始 | 部分页✔ |

**扫描页检测基准（每页独立判断）：**
```
页面含图片 且 (去水印后文本 < 50 个有效字符) → 扫描页 → OCR
否则 → 文本页 → 用原始 get_text()
```

### 3.4 页面图片与 OCR 是两个独立功能

| 功能 | 适用范围 | 目的 |
|------|----------|------|
| **页面渲染 PNG** | 所有 PDF 页面（无论扫描还是文本） | VLM 看图回答 + 前端展示 |
| **OCR 提文字** | 仅扫描页 | 给 ChromaDB 提供可检索的文本索引 |

---

## 四、分阶段实施

### Phase 1：页面渲染 + OCR 入库（入库侧改造）

**目标**：扫描版 PDF 入库后 ChromaDB 中有可检索的文本切片。

#### 1.1 新建 `app/core/rag/page_renderer.py`

```python
class PageRenderer:
    """PDF 页面渲染为图片"""
    
    async def render_all_pages(self, pdf_path: str, file_id: str) -> list[PageImage]:
        """
        将 PDF 每页渲染为 PNG 图片
        
        - 显示用：150 DPI，存储到 {data_dir}/knowledge/pages/{file_id}/
        - 返回 PageImage 列表（含 file_id, page_number, image_path）
        """
```

**存储结构**：
```
data/knowledge/pages/
  {file_id}/
    0001.png    # 第 1 页 @150dpi
    0002.png    # 第 2 页
    ...
```

**性能**：PyMuPDF `page.get_pixmap(dpi=150)` ~50ms/页，99 页 ≈ 5 秒。
**存储**：150dpi A4 ≈ 200-400KB/页，99 页 ≈ 20-40MB。

#### 1.2 新建 `app/core/rag/ocr_service.py`

```python
class OCRService:
    """PDF 扫描页 OCR 文字提取"""
    
    async def ocr_scanned_pages(
        self, 
        pdf_path: str, 
        scanned_page_numbers: list[int]
    ) -> dict[int, str]:
        """
        对扫描页进行 OCR，返回 {page_number: ocr_text}
        
        策略：
        - 渲染 @300dpi
        - 检测横排（w > h*1.2）则旋转 90°
        - 大图（>4000px 高）分块 OCR 再合并
        - 多进程并行（ProcessPoolExecutor）
        """
```

**并行策略**：
```python
# 使用 ProcessPoolExecutor 并行 OCR 不同页面
# 每个进程独立加载 RapidOCR 模型，避免 GIL 瓶颈
with ProcessPoolExecutor(max_workers=4) as pool:
    futures = {
        pool.submit(ocr_single_page, page_img): page_num
        for page_num, page_img in scanned_pages.items()
    }
    for future in as_completed(futures):
        page_num = futures[future]
        results[page_num] = future.result()
```

**预期加速**：4 进程并行，99 页从 67 分钟 → ~17 分钟。

#### 1.3 新建 `app/core/rag/scanned_page_detector.py`

```python
WATERMARK_PATTERNS = ["标准分享网", "www.bzfxw.com", "免费下载", "仅供参考", "试用版"]

class ScannedPageDetector:
    """检测 PDF 哪些页面是扫描版"""
    
    def detect(self, pdf_path: str) -> tuple[list[int], list[int]]:
        """
        返回 (text_pages, scanned_pages) 两个页码列表
        
        判断逻辑：
        - 页面含图片 且 文本 < 50 字符（去水印后 < 10 字符）→ 扫描页
        """
```

#### 1.4 修改 `app/core/rag/document_parser.py`

在 `_parse_pdf()` 中融合 OCR 结果：

```python
def _parse_pdf(self, file_path: Path) -> str:
    # 1. 原有逻辑：提取文本层
    # 2. [NEW] 检测扫描页
    # 3. [NEW] 对扫描页 OCR
    # 4. [NEW] 融合：文本层页面用原文本，扫描页面用 OCR 文本
    # 5. 统一添加 [PAGE:x] 标记
```

#### 1.5 修改 `app/core/rag/document_ingestor.py`

在步骤 2（解析文本）后插入页面渲染：

```python
# ========== 2. 解析文档文本（含 OCR） ==========
text_content = await self.parser.parse(file_path)

# ========== 2.5 [NEW] 页面渲染 ==========
if file_path.suffix.lower() == '.pdf':
    page_renderer = get_page_renderer()
    page_images = await page_renderer.render_all_pages(str(file_path), file_id)
    self.logger.info(f"页面渲染完成: {len(page_images)} pages")
```

#### 1.6 配置项（`config.py` → `RAGSettings`）

```python
# ============ 页面渲染 & OCR ============
page_render_enabled: bool = True          # 启用页面渲染
page_render_dpi: int = 150                # 显示用 DPI
ocr_enabled: bool = True                  # 启用 OCR（扫描页）
ocr_dpi: int = 300                        # OCR 用 DPI
ocr_max_workers: int = 4                  # OCR 并行进程数
ocr_tile_threshold: int = 4000            # 分块阈值（px）
ocr_det_db_box_thresh: float = 0.3        # 文字检测阈值
ocr_det_db_unclip_ratio: float = 1.6      # 文字检测扩展比
```

---

### Phase 2：检索侧页面图片收集

**目标**：文本 RAG 定位到相关 chunk 后，自动收集对应页码的页面图片。

#### 2.1 新建 `app/core/rag/page_image_collector.py`

```python
class PageImageCollector:
    """从 RAG 检索结果中收集相关页面图片"""
    
    def collect(
        self, 
        chunks: list[RerankedChunk], 
        file_id: str,
        expand_pages: int = 1,    # 邻页扩展数
        max_pages: int = 6        # 最多返回页数
    ) -> list[PageImageRef]:
        """
        输入：reranked chunks（带 page_numbers metadata）
        输出：去重排序的页面图片引用列表
        
        逻辑：
        1. 从 chunks 提取页码集合
        2. 邻页扩展（±expand_pages）
        3. 按 rerank score 加权排序
        4. 取 top max_pages
        5. 返回图片路径列表
        """
```

#### 2.2 修改 `app/skills/doc_skill.py`

在 `query_knowledge_base()` 返回结果中增加 `page_images` 字段：

```python
# 现有流程
reranked_chunks = await reranker.rerank(...)
expanded_context = context_expander.expand(...)

# [NEW] 收集页面图片
collector = get_page_image_collector()
page_images = collector.collect(reranked_chunks, file_id)

return {
    "context": expanded_context,
    "page_images": page_images,  # [NEW]
    ...
}
```

---

### Phase 3：多模态 VLM 回答生成

**目标**：将页面图片 + 文本上下文 + 用户问题一起送入多模态 LLM 生成回答。

#### 3.1 新建 `app/core/rag/multimodal_answerer.py`

```python
class MultimodalAnswerer:
    """多模态 LLM 回答生成"""
    
    async def answer(
        self, 
        query: str,
        text_context: str,
        page_images: list[PageImageRef],
        model: str = "qwen-vl-max"
    ) -> AnswerResult:
        """
        构建多模态 prompt 并调用 VLM
        
        Prompt 结构：
        [系统] 你是文档分析助手，根据文本片段和页面截图回答问题，标注页码
        [文本上下文] chunk1(第3页), chunk2(第5页)...
        [图片] page_3.png, page_5.png, page_12.png
        [用户] 问题
        """
```

#### 3.2 调用方式

仅当以下条件同时满足时走多模态路径：
1. 源文档是 PDF
2. 有页面图片（`page_images` 非空）
3. 配置启用了多模态回答 (`multimodal_answer_enabled=True`)

否则 fallback 到现有的纯文本 LLM 回答。

#### 3.3 前端展示

回答中附带页面图片引用：
```json
{
  "answer": "根据第10页表格，HRC 20.0 对应的抗拉强度为 774 N/mm²...",
  "citations": [
    {"page": 10, "image_url": "/api/pages/{file_id}/10"}
  ]
}
```

#### 3.4 新增 API 路由：页面图片访问

```python
# app/routers/knowledge/page_images.py
@router.get("/{file_id}/pages/{page_number}")
async def get_page_image(file_id: str, page_number: int):
    """获取文档页面渲染图片"""
```

---

## 五、文件清单

### 新建文件

| 文件 | Phase | 职责 |
|------|-------|------|
| `app/core/rag/page_renderer.py` | 1 | PDF 页面渲染为 PNG |
| `app/core/rag/ocr_service.py` | 1 | 扫描页 OCR（分块+并行） |
| `app/core/rag/scanned_page_detector.py` | 1 | 扫描页检测（水印过滤） |
| `app/core/rag/page_image_collector.py` | 2 | 检索后页面图片收集 |
| `app/core/rag/multimodal_answerer.py` | 3 | 多模态 VLM 回答生成 |
| `app/routers/knowledge/page_images.py` | 3 | 页面图片 API |

### 修改文件

| 文件 | Phase | 改动 |
|------|-------|------|
| `app/core/rag/document_parser.py` | 1 | `_parse_pdf()` 融合 OCR 文本 |
| `app/core/rag/document_ingestor.py` | 1 | 入库流程增加页面渲染步骤 |
| `app/config.py` | 1 | 新增 OCR / 页面渲染配置项 |
| `app/skills/doc_skill.py` | 2 | 检索结果附加页面图片 |
| `requirements.txt` | 1 | 新增 `rapidocr_onnxruntime` |

### 不改动的文件

| 文件 | 原因 |
|------|------|
| `semantic_chunker.py` | 输入仍是纯文本（OCR 后的），无需改动 |
| `hybrid_retriever.py` | 检索逻辑不变 |
| `image_linker.py` | 内嵌图片关联逻辑不变 |
| `image_service.py` | 内嵌图片提取与页面渲染是独立功能 |

---

## 六、依赖

### 新增 Python 包

```
rapidocr_onnxruntime>=1.2.0    # OCR 引擎（纯 CPU，~40MB）
```

### 已有依赖（无需新增）

```
PyMuPDF (fitz)    # 页面渲染（已有）
Pillow (PIL)      # 图片处理（已有）
numpy             # OCR 输入（已有）
```

---

## 七、性能预估

### 入库性能（99 页扫描版 PDF）

| 步骤 | 耗时 | 说明 |
|------|------|------|
| 页面渲染 @150dpi | ~5s | PyMuPDF，纯 CPU |
| 扫描检测 | <1s | 逐页 get_text + get_images |
| OCR @300dpi（串行） | ~67min | 平均 41s/页 |
| OCR @300dpi（4 并行） | **~17min** | ProcessPoolExecutor |
| 语义切分 | ~10s | 现有逻辑 |
| 向量化 | ~15s | API 调用 |
| ChromaDB 存储 | ~2s | 本地 |
| **总计** | **~18min** | 一次性成本 |

### 查询性能（不变 + 新增）

| 步骤 | 耗时 | 说明 |
|------|------|------|
| 文本 RAG 检索 | ~1.5s | 现有流程不变 |
| 页面图片收集 | <100ms | 文件系统读取 |
| 多模态 VLM 回答 | ~3-5s | 3-6 页图片 + 文本 |
| **总计** | **~5-7s** | 用户可感知 |

---

## 八、实施顺序

```
Phase 1（入库侧） ─── 约 2 天
  ├── 1.1 PageRenderer（页面渲染）
  ├── 1.2 ScannedPageDetector（扫描检测）
  ├── 1.3 OCRService（分块+并行 OCR）
  ├── 1.4 DocumentParser 改造（融合 OCR）
  ├── 1.5 DocumentIngestor 改造（增加渲染步骤）
  └── 1.6 配置项 + requirements.txt

Phase 2（检索侧） ─── 约 1 天
  ├── 2.1 PageImageCollector
  └── 2.2 DocSkill 改造

Phase 3（回答侧） ─── 约 2 天
  ├── 3.1 MultimodalAnswerer
  ├── 3.2 页面图片 API 路由
  └── 3.3 前端适配（页面图片展示）
```

---

## 九、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| OCR 入库太慢 | 用户等待时间长 | 后台异步 + 进度通知；并行加速 |
| OCR 中文识别不准 | 检索质量下降 | OCR 文本仅用于粗筛，精确回答靠 VLM 看原图 |
| VLM API 费用 | 成本增加 | 仅查询时调用，每次 3-6 页；可配置开关 |
| 存储空间增加 | 磁盘占用 | 150dpi PNG 约 20-40MB/100页，可接受 |
| 多模态 LLM 回答质量 | 不如纯文本精准 | 保留 fallback 到纯文本 LLM 的通路 |
