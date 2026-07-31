import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const workspaceRoot = "\\\\sshfs.kr\\root@115.120.248.123\\root\\AiData\\DeluData_pro";
const outputDir = process.env.KB_FEATURE_OUTPUT_DIR || path.join(workspaceRoot, "outputs", "knowledge_base_feature_list");
const outputPath = path.join(outputDir, "DeluData_知识库功能清单.xlsx");

const rows = [
  ["知识库门户与视图", "知识库总览", "按公共、部门、个人等范围展示知识库概况和统计数据，帮助管理员快速了解文档沉淀规模与入口分布。", "知识库页面 - 总览", "frontend/src/pages/KnowledgeBasePage.tsx; backend/app/api/knowledge/stats.py"],
  ["知识库门户与视图", "仓库文件视图", "以文件夹树方式浏览知识库内容，支持进入不同知识范围查看文件和目录结构。", "知识库页面 - 仓库视图", "frontend/src/pages/KnowledgeBasePage.tsx; backend/app/api/knowledge/folders.py"],
  ["知识库门户与视图", "知识图谱视图", "展示文件之间的显式关联关系，支持查看节点和关系边，辅助理解资料之间的业务联系。", "知识库页面 - 图谱视图", "backend/app/api/knowledge/relationships.py; frontend/src/services/knowledgeService.ts"],
  ["知识库门户与视图", "Wiki 视图", "以实体页方式呈现从高频、稳定、已治理知识中沉淀出的企业语义层，区分于普通原文文件库。", "知识库页面 - Wiki 视图", "frontend/src/components/wiki/WikiView.tsx; backend/app/api/wiki/router.py"],

  ["文件与目录管理", "文件上传", "支持上传 PDF、Word、Markdown、TXT 等知识文件，上传后创建入库任务并返回处理状态。", "上传弹窗 / /knowledge/upload", "frontend/src/services/knowledgeService.ts; backend/app/api/knowledge/documents.py"],
  ["文件与目录管理", "上传权限与大小限制", "根据租户工作区配置控制是否允许上传，并限制单文件大小，避免超出知识库治理策略。", "上传前校验", "frontend/src/pages/KnowledgeBasePage.tsx; backend/app/services/workspace_knowledge_governance_service.py"],
  ["文件与目录管理", "文件夹创建", "支持创建公共、部门或个人可见范围下的知识库文件夹，用于分类管理资料。", "仓库视图 - 新建文件夹", "backend/app/api/knowledge/folders.py; frontend/src/services/knowledgeService.ts"],
  ["文件与目录管理", "文件/文件夹重命名", "支持对知识文件和文件夹重命名，便于维护业务可读的资料名称。", "仓库视图 - 重命名", "backend/app/api/knowledge/documents.py; backend/app/api/knowledge/folders.py"],
  ["文件与目录管理", "文件/文件夹移动", "支持调整知识文件或目录所在位置，维护资料分类和层级结构。", "仓库视图 - 移动", "backend/app/api/knowledge/documents.py; backend/app/api/knowledge/folders.py"],
  ["文件与目录管理", "批量删除", "支持批量删除文件或文件夹，并保留删除状态、失败原因等过程信息。", "仓库视图 - 批量操作", "backend/app/api/knowledge/documents.py; backend/app/api/knowledge/folders.py"],
  ["文件与目录管理", "文件元数据维护", "支持维护文档类型、业务域、部门、密级、生效期、负责人、外部编号、可见性等元数据。", "文件详情 / 元数据编辑", "backend/app/api/knowledge/schemas.py; backend/app/api/knowledge/documents.py"],
  ["文件与目录管理", "文档说明维护", "支持更新文件描述信息，便于用户在浏览和检索前理解文档用途。", "文件说明编辑", "backend/app/api/knowledge/documents.py"],
  ["文件与目录管理", "文件访问与下载", "提供原始文件、下载地址和鉴权访问 URL，支持前端预览和用户下载。", "文件详情 / 下载", "backend/app/api/knowledge/documents.py"],
  ["文件与目录管理", "文件内容编辑", "支持对可编辑文档内容进行更新，并触发后续索引刷新。", "文件编辑", "backend/app/api/knowledge/documents.py"],

  ["入库与索引", "异步入库任务", "上传后进入入库任务队列，记录 pending、running、succeeded、failed、cancelled 等状态。", "入库任务轮询", "backend/app/models/knowledge/ingestion_task.py; backend/app/api/knowledge/tasks.py"],
  ["入库与索引", "入库进度跟踪", "按 queued、preclean、parsing、ocr、chunking、embedding、storing、completed 等阶段返回进度，便于前端展示处理状态。", "任务状态接口", "backend/app/api/knowledge/schemas.py; frontend/src/services/knowledgeService.ts"],
  ["入库与索引", "入库任务取消", "支持取消正在排队或执行中的入库任务，避免错误文件或大文件继续消耗资源。", "/knowledge/tasks/{task_id}/cancel", "backend/app/api/knowledge/tasks.py; backend/app/api/knowledge/documents.py"],
  ["入库与索引", "文档解析", "对上传文件进行文本抽取，为后续切片、向量化和检索提供原文内容。", "入库服务", "backend/app/services/ingestion_service.py; backend/app/core/rag/document_parser.py"],
  ["入库与索引", "OCR 识别", "对扫描版或图片型 PDF 页面进行 OCR，补齐没有文本层的资料，使其可检索。", "入库 OCR 阶段", "backend/app/core/rag/ocr_pool.py; backend/app/core/rag/ocr_worker.py"],
  ["入库与索引", "PDF 页面索引", "记录 PDF 页面图像、页码和 PageIndex 状态，支持按页引用、预览和多模态证据定位。", "PDF / PageIndex", "backend/app/core/rag/pdf_detailed_pipeline.py; backend/app/models/knowledge/graph.py"],
  ["入库与索引", "语义分块", "按段落、标题、页码和语义相似度切分文档，保留 chunk 与页码、前后文的关系。", "RAG 切片", "backend/app/core/rag/semantic_chunker.py"],
  ["入库与索引", "向量化与存储", "将文档切片生成 embedding 并写入向量库，为语义检索提供索引基础。", "入库 embedding/storing 阶段", "backend/app/services/ingestion_service.py; backend/app/core/rag/document_ingestor.py"],
  ["入库与索引", "重新索引", "支持对指定文件重新解析、切片和更新向量，修复旧索引或元数据变化后的检索效果。", "/knowledge/files/{file_id}/reindex", "backend/app/api/knowledge/documents.py"],
  ["入库与索引", "入库前清理", "重建索引前清理旧切片、旧向量、图片和相关派生数据，避免重复召回或脏数据。", "pre_cleanup_document_data", "backend/app/services/ingestion_service.py"],
  ["入库与索引", "文件图片管理", "抽取并记录文档中的图片或页面图，提供图片读取和元数据接口。", "/knowledge/images/*", "backend/app/api/knowledge/images.py; backend/app/models/knowledge/graph.py"],

  ["RAG 检索与回答", "知识库范围选择", "聊天时可选择文档文件夹、文件、Wiki 域或 Wiki 实体页作为检索范围，约束回答依据。", "聊天区 KnowledgeScopePicker", "frontend/src/components/chat/KnowledgeScopePicker.tsx; frontend/src/components/chat/WikiScopePanel.tsx"],
  ["RAG 检索与回答", "文档范围合并", "合并会话级和步骤级文档范围，标准化 file_ids、folder_ids、domains、wiki_slugs 等条件。", "DocScope", "backend/app/services/doc_scope.py"],
  ["RAG 检索与回答", "查询改写", "对用户问题生成检索友好的查询变体，提高召回稳定性。", "RAG 检索链路", "backend/app/core/rag/query_rewriter.py"],
  ["RAG 检索与回答", "混合检索", "结合向量检索和 BM25 关键词检索，并用 RRF 等方式合并候选结果，兼顾语义和精确词命中。", "HybridRetriever", "backend/app/core/rag/hybrid_retriever.py"],
  ["RAG 检索与回答", "重排序", "对召回候选进行 rerank，保留更相关的证据片段进入最终上下文。", "Reranker", "backend/app/core/rag/reranker.py"],
  ["RAG 检索与回答", "上下文扩展", "基于 chunk 前后链路扩展相邻上下文，避免答案只看到孤立片段。", "ContextExpander", "backend/app/core/rag/context_expander.py"],
  ["RAG 检索与回答", "引用溯源", "答案可携带来源文件、页码、切片预览等信息，支持用户核验证据。", "回答证据 / citations", "backend/app/api/wiki/schemas.py; backend/app/core/rag/base_retriever.py"],
  ["RAG 检索与回答", "多模态 PDF 证据", "对 PDF 页面图、OCR 文本和命中页码进行关联，支持图文混合资料的证据定位。", "PDF 多模态 RAG", "docs/pdf-multimodal-rag-implementation-plan.md; backend/app/core/rag/pdf_detailed_pipeline.py"],

  ["Wiki 语义沉淀", "Wiki 编译", "从指定文件或整个工作区已索引文档中生成/更新 Wiki 实体页，沉淀高复用企业知识。", "/wiki/compile", "backend/app/api/wiki/router.py; backend/app/services/wiki_service.py"],
  ["Wiki 语义沉淀", "异步 Wiki 编译任务", "支持 Wiki 编译入队、任务去重、轮询状态、进度上报和失败记录，避免长请求阻塞。", "/wiki/compile?async=true", "backend/app/services/wiki_compile_queue_service.py; backend/app/api/wiki/router.py"],
  ["Wiki 语义沉淀", "Wiki 编译取消", "支持取消排队或运行中的 Wiki 编译任务，并返回取消结果。", "/wiki/tasks/{task_id}/cancel", "backend/app/api/wiki/router.py; backend/app/services/wiki_compile_queue_service.py"],
  ["Wiki 语义沉淀", "Wiki 实体页列表", "按状态、关键字、业务域、知识范围等条件列出 Wiki 实体页。", "WikiIndexView / /wiki/pages", "frontend/src/components/wiki/WikiIndexView.tsx; backend/app/api/wiki/router.py"],
  ["Wiki 语义沉淀", "Wiki 实体页详情", "展示实体页标题、摘要、正文、别名、业务域、状态、来源、入链、出链和修订记录。", "WikiPageDetailView", "backend/app/api/wiki/schemas.py; frontend/src/components/wiki/WikiPageDetailView.tsx"],
  ["Wiki 语义沉淀", "Wiki 实体页编辑", "支持更新 Wiki 页标题、摘要、正文、业务域、状态和提交说明，形成版本化知识维护。", "/wiki/pages/{slug}", "backend/app/api/wiki/router.py; backend/app/api/wiki/schemas.py"],
  ["Wiki 语义沉淀", "Wiki 来源证据", "每个 Wiki 页保留来源文件、证据切片、页码、可见性、部门、密级、生效期等来源信息。", "Wiki sources", "backend/app/api/wiki/schemas.py; backend/app/models/wiki/wiki_page_source.py"],
  ["Wiki 语义沉淀", "Wiki 修订记录", "记录 Wiki 页版本、提交人、提交说明和提交时间，便于审计和回滚参考。", "Wiki revisions", "backend/app/models/wiki/wiki_revision.py; backend/app/api/wiki/schemas.py"],
  ["Wiki 语义沉淀", "Wiki 链接关系", "维护实体页之间的入链、出链、关系类型、置信度和状态，形成企业知识网络。", "Wiki links", "backend/app/models/wiki/wiki_link.py; backend/app/api/wiki/schemas.py"],
  ["Wiki 语义沉淀", "Wiki 图谱", "以节点和边展示 Wiki 实体、来源文件和知识关系，支持按范围查看知识结构。", "WikiGraphView / /wiki/graph", "frontend/src/components/wiki/WikiGraphView.tsx; backend/app/api/wiki/router.py"],
  ["Wiki 语义沉淀", "Wiki 域聚合", "按业务域聚合实体页数量和样例标题，为聊天范围选择和知识运营提供入口。", "/wiki/domains", "backend/app/api/wiki/router.py; frontend/src/components/chat/WikiScopePanel.tsx"],

  ["Wiki-First 路由", "RAG/Wiki/Both 路由", "在知识问答前判断走原文 RAG、Wiki 实体页或两者混合，使概念性问题优先使用稳定知识，原文细节问题优先使用证据层。", "KnowledgeRouter", "backend/app/supervisor/nodes/knowledge_router.py; docs/m3-wiki-first-summary.md"],
  ["Wiki-First 路由", "规则/LLM/Fallback 路由策略", "通过开关、用户范围、关键词规则、轻量 LLM 分类和兜底路径共同决定知识检索路径。", "WikiSettings / KnowledgeRouter", "docs/m3-wiki-first-summary.md; backend/app/config.py"],
  ["Wiki-First 路由", "显式范围强制路由", "当用户选择 Wiki 域或实体页时，系统直接按所选范围装载，减少额外路由判断成本。", "WikiScopePanel", "frontend/src/components/chat/WikiScopePanel.tsx; backend/app/services/doc_scope.py"],
  ["Wiki-First 路由", "Wiki Gap 识别", "当用户问题命中知识盲区或 Wiki 未沉淀时记录 Gap 信号，为后续自动编译和治理提供线索。", "wiki_gap_signal", "docs/m3-wiki-first-summary.md; docs/m4-wiki-governance-summary.md"],
  ["Wiki-First 路由", "Wiki Gap 自动入队", "回答链路结束后将有效 Gap 信号转换为后台 Wiki 编译任务，逐步补齐高频知识盲区。", "wiki_gap_autoenqueue", "backend/app/services/wiki_compile_queue_service.py; docs/m4-wiki-governance-summary.md"],

  ["Wiki 治理", "候选页治理", "列出待治理 Wiki 候选页，展示治理评分、来源范围、冲突/重复等状态，供管理员处理。", "WikiCandidateGovernanceCard", "frontend/src/components/wiki/WikiCandidateGovernanceCard.tsx; backend/app/services/wiki_service.py"],
  ["Wiki 治理", "治理动作", "支持发布、验证、转草稿、归档、废弃、提升、取消提升、标记重复、标记冲突、合并等操作。", "/wiki/pages/{slug}/governance-action", "backend/app/api/wiki/schemas.py; backend/app/api/wiki/router.py"],
  ["Wiki 治理", "Wiki 健康巡检", "运行 lint 检查孤立页、断链、冲突等问题，生成工作区级 Wiki 健康报告。", "/wiki/lint", "backend/app/api/wiki/router.py; backend/app/api/wiki/schemas.py"],
  ["Wiki 治理", "孤儿页清理", "扫描并清理因源文件删除或失效产生的孤立 Wiki 页，降低知识污染。", "/wiki/purge-orphans", "backend/app/api/wiki/router.py; backend/app/services/wiki_service.py"],
  ["Wiki 治理", "批量归档", "支持按 slug 列表或业务域批量归档 Wiki 页，用于知识过期、错误沉淀或域级下线场景。", "/wiki/pages/batch-archive", "backend/app/api/wiki/router.py; backend/app/api/wiki/schemas.py"],
  ["Wiki 治理", "配额控制", "按工作区限制 Wiki 页总量和单页出链数量，编译时截断超限内容，防止实体膨胀。", "/wiki/quota", "backend/app/services/wiki_service.py; docs/m4-wiki-governance-summary.md"],
  ["Wiki 治理", "配额可视化", "在 Wiki 路由健康卡中展示实体页使用量、单页出链上限和接近上限告警。", "WikiRouteHealthCard", "frontend/src/components/wiki/WikiRouteHealthCard.tsx; frontend/src/services/wikiService.ts"],

  ["诊断与运营", "检索诊断", "输入问题后展示实际知识路径、权限过滤、元数据过滤、命中片段、保留/丢弃原因和最终上下文。", "/wiki/diagnostics/retrieval", "backend/app/services/retrieval_diagnostics_service.py; backend/app/api/wiki/schemas.py"],
  ["诊断与运营", "会话诊断", "查看历史会话的知识路径、使用的 RAG/Wiki 上下文、回退情况、修复轨迹和潜在问题。", "/wiki/diagnostics/conversation", "backend/app/api/wiki/router.py; backend/app/api/wiki/schemas.py"],
  ["诊断与运营", "路由健康度指标", "按 7/14/30 天统计 RAG/Wiki/Both 命中量、平均时延、Wiki 命中率、Gap 率和回退情况。", "WikiRouteHealthCard / /wiki/metrics/route", "backend/app/services/wiki_metrics_service.py; frontend/src/components/wiki/WikiRouteHealthCard.tsx"],
  ["诊断与运营", "文档统计", "统计全局、个人、部门等知识范围下的文档数量，用于知识库运营看板。", "/knowledge/stats", "backend/app/api/knowledge/stats.py"],
  ["诊断与运营", "文档切片查看", "按文档查看已生成的 chunks，辅助排查解析、分块和召回质量问题。", "/knowledge/documents/{document_id}/chunks", "backend/app/api/knowledge/stats.py"],
  ["诊断与运营", "文档来源信息", "展示文档归属人、部门、可见性、创建更新时间、切片覆盖等来源信息，帮助审计答案依据。", "/knowledge/documents/{document_id}/origin", "backend/app/api/knowledge/stats.py; backend/app/api/knowledge/schemas.py"],

  ["权限与治理", "工作区隔离", "知识库文件、Wiki 页、任务、路由指标等都带 workspace_id，确保不同租户/工作区数据隔离。", "TenantMixin / workspace_id", "backend/app/models/knowledge; backend/app/models/wiki"],
  ["权限与治理", "可见范围控制", "文件夹和文件支持 public、dept、private 等可见范围，并结合部门、负责人进行访问控制。", "visibility / dept_id / owner_id", "backend/app/api/knowledge/schemas.py; backend/app/models/knowledge/graph.py"],
  ["权限与治理", "密级与有效期", "文档元数据包含 confidentiality_level、effective_from、effective_until，支持后续按密级和有效期过滤。", "文件元数据", "backend/app/api/knowledge/schemas.py"],
  ["权限与治理", "知识库功能开关", "平台可配置上传、删除、重命名、移动、新建文件夹等能力开关，前端根据租户能力控制按钮。", "workspace_features", "frontend/src/pages/KnowledgeBasePage.tsx; backend/app/services/workspace_knowledge_governance_service.py"],
  ["权限与治理", "知识库容量治理", "评估存储配额和单文件上传大小，返回使用情况和是否允许操作，避免工作区无限制增长。", "WorkspaceKnowledgeGovernanceService", "backend/app/services/workspace_knowledge_governance_service.py"],
  ["权限与治理", "权限贯穿检索", "检索诊断和回答链路体现权限与元数据过滤结果，保证未授权文档不进入最终上下文。", "retrieval diagnostics permissions", "backend/app/services/retrieval_diagnostics_service.py; backend/app/services/doc_scope.py"],
];

const headers = ["一级模块", "功能", "功能描述", "典型入口/对象", "依据"];

await fs.mkdir(outputDir, { recursive: true });

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("知识库功能清单");
sheet.showGridLines = false;

sheet.getRange("A1:E1").merge();
sheet.getRange("A1").values = [["DeluData 知识库功能清单"]];
sheet.getRange("A2:E2").merge();
sheet.getRange("A2").values = [["范围说明：本清单聚焦知识库、RAG、Wiki、治理与诊断能力；已排除问数、Text-to-SQL、数据库分析功能。"]];

sheet.getRange("A4:E4").values = [headers];
sheet.getRangeByIndexes(4, 0, rows.length, headers.length).values = rows;

const lastRow = rows.length + 4;
const fullRange = sheet.getRange(`A4:E${lastRow}`);
const table = sheet.tables.add(`A4:E${lastRow}`, true, "KnowledgeBaseFeatureList");
table.style = "TableStyleMedium2";
table.showFilterButton = true;

sheet.freezePanes.freezeRows(4);

sheet.getRange("A1:E1").format = {
  fill: "#17324D",
  font: { bold: true, color: "#FFFFFF", size: 18 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
sheet.getRange("A2:E2").format = {
  fill: "#EAF2F8",
  font: { color: "#1F2937", size: 10 },
  horizontalAlignment: "left",
  verticalAlignment: "center",
  wrapText: true,
};
sheet.getRange("A4:E4").format = {
  fill: "#2F5D7C",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
sheet.getRange(`A5:E${lastRow}`).format = {
  font: { size: 10, color: "#111827" },
  verticalAlignment: "top",
  wrapText: true,
};
sheet.getRange(`A4:E${lastRow}`).format.borders = {
  insideHorizontal: { style: "thin", color: "#D7DEE8" },
  insideVertical: { style: "thin", color: "#E5E7EB" },
  top: { style: "medium", color: "#94A3B8" },
  bottom: { style: "medium", color: "#94A3B8" },
  left: { style: "thin", color: "#CBD5E1" },
  right: { style: "thin", color: "#CBD5E1" },
};

sheet.getRange("A:A").format.columnWidth = 18;
sheet.getRange("B:B").format.columnWidth = 22;
sheet.getRange("C:C").format.columnWidth = 58;
sheet.getRange("D:D").format.columnWidth = 30;
sheet.getRange("E:E").format.columnWidth = 46;
sheet.getRange("A1").format.rowHeight = 30;
sheet.getRange("A2").format.rowHeight = 34;
sheet.getRange("A4").format.rowHeight = 24;
sheet.getRange(`A5:A${lastRow}`).format.rowHeight = 48;

sheet.getRange(`A5:A${lastRow}`).format = {
  fill: "#F8FAFC",
  font: { bold: true, color: "#334155", size: 10 },
  verticalAlignment: "top",
  wrapText: true,
};

const summary = workbook.worksheets.add("模块汇总");
summary.showGridLines = false;
const moduleCounts = new Map();
for (const row of rows) {
  moduleCounts.set(row[0], (moduleCounts.get(row[0]) || 0) + 1);
}
const summaryRows = [...moduleCounts.entries()].map(([module, count]) => [module, count]);
summary.getRange("A1:B1").merge();
summary.getRange("A1").values = [["知识库功能模块汇总"]];
summary.getRange("A3:B3").values = [["一级模块", "功能数量"]];
summary.getRangeByIndexes(3, 0, summaryRows.length, 2).values = summaryRows;
const summaryLastRow = summaryRows.length + 3;
summary.tables.add(`A3:B${summaryLastRow}`, true, "KnowledgeBaseModuleSummary").style = "TableStyleMedium4";
summary.getRange("A1:B1").format = {
  fill: "#17324D",
  font: { bold: true, color: "#FFFFFF", size: 16 },
  horizontalAlignment: "center",
};
summary.getRange("A3:B3").format = {
  fill: "#2F5D7C",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
};
summary.getRange(`A4:B${summaryLastRow}`).format = {
  font: { size: 10, color: "#111827" },
  verticalAlignment: "center",
};
summary.getRange("A:A").format.columnWidth = 24;
summary.getRange("B:B").format.columnWidth = 12;
summary.getRange("A1").format.rowHeight = 30;

const errorScan = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 20 },
  summary: "formula error scan",
});
console.log(errorScan.ndjson);

const preview = await workbook.render({
  sheetName: "知识库功能清单",
  range: "A1:E24",
  scale: 1,
  format: "png",
});
await fs.writeFile(path.join(outputDir, "preview.png"), new Uint8Array(await preview.arrayBuffer()));

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(outputPath);
