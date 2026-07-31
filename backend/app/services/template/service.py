"""
模板服务层

职责：
- 模板 CRUD 业务逻辑
- 模板分组管理
- 组合分析器、转换器、渲染器

设计原则遵循：
- Async First: 所有 IO 操作异步
- Design Rigor: 与 Router 层分离
"""
import os
import posixpath
import json
import uuid
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from app.config import get_settings
from app.core.storage.service import get_storage_service
from app.core.utils.storage_path import (
    build_oss_path,
    is_oss_path,
    normalize_storage_path,
    parse_oss_path,
    resolve_storage_path,
)
from app.models.config.template import (
    Template, TemplateCreate, TemplateUpdate,
    create_template_async, get_template_async, list_templates_async,
    update_template_async, delete_template_async, match_templates_async,
)
from app.models.config.template_group import (
    TemplateGroup, TemplateGroupCreate, TemplateGroupUpdate,
    create_template_group_async, get_template_group_async, list_template_groups_async,
    update_template_group_async, delete_template_group_async,
)
from .analyzer import get_template_analyzer
from .converter import get_template_converter
from .renderer import get_template_renderer
from .detector import get_blank_field_detector, CandidateField

logger = logging.getLogger(__name__)

# 模板存储目录
settings = get_settings()
TEMPLATE_STORAGE_DIR = os.path.join(settings.app.data_dir, "templates")


class TemplateService:
    """模板服务"""

    def __init__(self):
        self._analyzer = None
        self._converter = None
        self._renderer = None
        self._detector = None

    @property
    def analyzer(self):
        if self._analyzer is None:
            self._analyzer = get_template_analyzer()
        return self._analyzer

    @property
    def converter(self):
        if self._converter is None:
            self._converter = get_template_converter()
        return self._converter

    @property
    def renderer(self):
        if self._renderer is None:
            self._renderer = get_template_renderer()
        return self._renderer

    @property
    def detector(self):
        """空白字段检测器"""
        if self._detector is None:
            self._detector = get_blank_field_detector()
        return self._detector

    @property
    def task_manager(self):
        """任务管理器"""
        if not hasattr(self, '_task_manager') or self._task_manager is None:
            from .task_manager import get_task_manager
            self._task_manager = get_task_manager()
        return self._task_manager

    @property
    def task_executor(self):
        """任务执行器"""
        if not hasattr(self, '_task_executor') or self._task_executor is None:
            from .task_executor import get_task_executor
            self._task_executor = get_task_executor()
        return self._task_executor

    @property
    def storage_service(self):
        if not hasattr(self, '_storage_service') or self._storage_service is None:
            self._storage_service = get_storage_service()
        return self._storage_service

    @asynccontextmanager
    async def _materialized_template(self, template: Template):
        suffix = os.path.splitext(template.file_path or '')[1] or f'.{template.file_type}'
        async with self.storage_service.materialize(
            template.file_path,
            suffix=suffix,
            filename=os.path.basename(template.file_path),
        ) as local_path:
            yield template.model_copy(update={'file_path': local_path})

    @asynccontextmanager
    async def _materialized_path(self, file_path: str, file_type: Optional[str] = None):
        suffix = os.path.splitext(file_path or '')[1] or (f'.{file_type}' if file_type else '')
        async with self.storage_service.materialize(
            file_path,
            suffix=suffix,
            filename=os.path.basename(file_path),
        ) as local_path:
            yield local_path

    # ========== 模板 CRUD ==========

    async def upload_template(
        self,
        filename: str,
        file_content: bytes,
        name: str,
        workspace_id: str,
        user_id: str,
        description: Optional[str] = None,
        keywords: Optional[str] = None,
        variables_schema_json: Optional[str] = None,
        example_context_json: Optional[str] = None,
        group_id: Optional[int] = None,
    ) -> Template:
        """上传模板"""
        # 验证文件类型
        ext = filename.split('.')[-1].lower()
        if ext not in ['docx', 'xlsx']:
            raise ValueError("仅支持 .docx 和 .xlsx 格式")

        # 确保存储目录存在
        workspace_dir = os.path.join(TEMPLATE_STORAGE_DIR, workspace_id)
        await asyncio.to_thread(os.makedirs, workspace_dir, exist_ok=True)

        # 生成文件路径并保存
        file_id = str(uuid.uuid4())[:8]
        saved_filename = f"{file_id}_{filename}"
        file_path = os.path.join(workspace_dir, saved_filename)

        def _write_file():
            with open(file_path, 'wb') as f:
                f.write(file_content)
        await asyncio.to_thread(_write_file)

        # 解析 JSON 参数
        schema_dict = None
        context_dict = None

        if variables_schema_json:
            try:
                schema_dict = json.loads(variables_schema_json)
            except json.JSONDecodeError as e:
                raise ValueError(f"variables_schema JSON 格式错误: {str(e)}")

        if example_context_json:
            try:
                context_dict = json.loads(example_context_json)
            except json.JSONDecodeError as e:
                raise ValueError(f"example_context JSON 格式错误: {str(e)}")

        # 自动分析变量
        if not schema_dict:
            analysis = await self.analyzer.analyze_async(file_path, ext)
            schema_dict = analysis['suggested_schema']

        storage_path = ""

        # 创建数据库记录
        template_create = TemplateCreate(
            name=name,
            description=description,
            file_type=ext,
            keywords=keywords,
            variables_schema=schema_dict,
            example_context=context_dict,
            group_id=group_id
        )

        try:
            storage_path = await self.storage_service.upload_file(
                file_path,
                self.storage_service.build_template_object_key(workspace_id, file_id, saved_filename),
                content_type='application/octet-stream',
            )
            return await create_template_async(
                template=template_create,
                file_path=storage_path,
                workspace_id=workspace_id,
                user_id=user_id
            )
        except Exception:
            if storage_path:
                await self.storage_service.delete(storage_path)
            raise
        finally:
            Path(file_path).unlink(missing_ok=True)

    async def get_template(self, template_id: int, workspace_id: str) -> Optional[Template]:
        """获取模板（带权限校验）"""
        template = await get_template_async(template_id)
        if not template or template.workspace_id != workspace_id:
            return None
        return template

    async def list_templates(self, workspace_id: str, active_only: bool = False) -> List[Template]:
        """获取模板列表"""
        return await list_templates_async(workspace_id=workspace_id, active_only=active_only)

    async def match_templates(self, workspace_id: str, query: str) -> List[Template]:
        """匹配模板"""
        return await match_templates_async(workspace_id=workspace_id, query=query)

    async def update_template(
        self, template_id: int, request: TemplateUpdate, workspace_id: str
    ) -> Optional[Template]:
        """更新模板"""
        existing = await get_template_async(template_id)
        if not existing:
            raise ValueError("模板不存在")
        if existing.workspace_id != workspace_id:
            return None
        return await update_template_async(template_id, request)

    async def auto_compile_template(
        self,
        template_id: int,
        workspace_id: str,
        bindings: Optional[List[Dict[str, Any]]] = None,
        output_name: Optional[str] = None,
        update_schema: bool = True,
        min_confidence: float = 0.5
    ) -> Optional[Tuple[str, str]]:
        """
        自动编译模板（优先使用 bindings，否则走 LLM 检测）
        """
        template = await self.get_template(template_id, workspace_id)
        if not template:
            raise ValueError("模板不存在或无权访问")
        if template.file_type.lower() != "docx":
            return None

        if bindings is None:
            bindings = getattr(template, "bindings", None)

        if bindings:
            compiled_path, compiled_name = await self.compile_template(
                template, bindings, output_name
            )
            if update_schema:
                schema = template.variables_schema or {}
                if isinstance(schema, str):
                    try:
                        schema = json.loads(schema)
                    except Exception:
                        schema = {}
                for b in bindings:
                    key = b.get("key") or b.get("var") or b.get("name")
                    if not key:
                        continue
                    schema[key] = {
                        "type": b.get("type") or "text",
                        "desc": b.get("label") or ""
                    }
                await update_template_async(
                    template_id,
                    TemplateUpdate(variables_schema=schema)
                )
            return compiled_path, compiled_name

        candidates = await self.detect_blank_fields_llm(
            template_id, workspace_id, min_confidence=min_confidence
        )
        if not candidates:
            logger.warning(f"自动编译失败：未检测到字段 template_id={template_id}")
            return None
        return await self.confirm_llm_detected_fields(
            template_id=template_id,
            workspace_id=workspace_id,
            candidates=candidates,
            output_name=output_name,
            update_schema=update_schema
        )

    async def delete_template(self, template_id: int, workspace_id: str) -> Tuple[bool, str]:
        """删除模板"""
        existing = await get_template_async(template_id)
        if not existing:
            return (False, "模板不存在")
        if existing.workspace_id != workspace_id:
            return (False, "无权删除此模板")

        file_path_to_delete = existing.file_path
        success = await delete_template_async(template_id)
        if not success:
            return (False, "删除失败")

        # 清理文件
        if file_path_to_delete:
            try:
                await self.storage_service.delete(file_path_to_delete)
                logger.info(f"删除模板文件: {file_path_to_delete}")
            except Exception as e:
                logger.warning(f"删除模板文件失败: {e}")

        return (True, "模板已删除")

    # ========== 分析/转换/渲染代理 ==========

    async def analyze_template_variables_async(self, file_path: str, file_type: str) -> Dict[str, Any]:
        """分析模板变量"""
        async with self._materialized_path(file_path, file_type=file_type) as local_path:
            return await self.analyzer.analyze_async(local_path, file_type)

    async def docx_to_html_with_mapping(self, file_path: str) -> Dict[str, Any]:
        """转换 docx 为 HTML"""
        async with self._materialized_path(file_path, file_type='docx') as local_path:
            return await self.converter.docx_to_html_with_mapping(local_path)

    async def render_template_to_temp(self, template: Template, context: Dict[str, Any]) -> str:
        """渲染模板到临时文件"""
        async with self._materialized_template(template) as local_template:
            return await self.renderer.render_to_temp(local_template, context)

    async def compile_template(
        self, template: Template, bindings: List[Dict[str, Any]], output_name: Optional[str] = None
    ) -> Tuple[str, str]:
        """编译模板"""
        async with self._materialized_template(template) as local_template:
            local_compiled_path, compiled_name = await self.renderer.compile(local_template, bindings, output_name)

            compiled_storage_path = self._build_compiled_storage_path(
                template.file_path,
                compiled_name,
            )

            try:
                await self.storage_service.replace_from_local_file(
                    compiled_storage_path,
                    local_compiled_path,
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            finally:
                should_cleanup_local_copy = is_oss_path(compiled_storage_path)
                if not should_cleanup_local_copy:
                    target_path = os.path.normcase(os.path.normpath(resolve_storage_path(compiled_storage_path)))
                    source_path = os.path.normcase(os.path.normpath(local_compiled_path))
                    should_cleanup_local_copy = source_path != target_path
                if should_cleanup_local_copy:
                    Path(local_compiled_path).unlink(missing_ok=True)

            return compiled_storage_path, compiled_name

    def _build_compiled_storage_path(self, file_path: str, compiled_name: str) -> str:
        compiled_filename = Path(compiled_name).name
        if is_oss_path(file_path):
            bucket, key = parse_oss_path(file_path)
            key_dir = posixpath.dirname(key)
            return build_oss_path(bucket, posixpath.join(key_dir, compiled_filename))

        base_dir = os.path.dirname(file_path)
        return normalize_storage_path(os.path.join(base_dir, compiled_filename))

    async def confirm_llm_detected_fields(
        self,
        template_id: int,
        workspace_id: str,
        candidates: List[CandidateField],
        output_name: Optional[str] = None,
        update_schema: bool = True
    ) -> Tuple[str, str]:
        """
        确认 LLM 检测结果并编译模板

        Args:
            template_id: 模板 ID
            workspace_id: 工作空间 ID
            candidates: 用户确认的候选字段
            output_name: 输出文件名
            update_schema: 是否更新 variables_schema
        """
        template = await self.get_template(template_id, workspace_id)
        if not template:
            raise ValueError("模板不存在或无权访问")
        if template.file_type.lower() != "docx":
            raise ValueError("仅支持 DOCX 模板的编译")

        bindings: List[Dict[str, Any]] = []
        for c in candidates or []:
            bindings.append({
                "key": c.key,
                "label": c.label,
                "type": c.type,
                "location": {
                    "type": c.location.type,
                    "paragraph_index": c.location.paragraph_index,
                    "table_index": c.location.table_index,
                    "row_index": c.location.row_index,
                    "cell_index": c.location.cell_index,
                    "selected_text": c.location.selected_text,
                    "mapping_id": c.location.mapping_id
                },
                "selected_text": c.location.selected_text
            })

        compiled_path, compiled_name = await self.compile_template(
            template, bindings, output_name
        )

        if update_schema:
            schema = template.variables_schema or {}
            if isinstance(schema, str):
                try:
                    schema = json.loads(schema)
                except Exception:
                    schema = {}

            for c in candidates or []:
                if not c.key:
                    continue
                schema[c.key] = {
                    "type": c.type or "text",
                    "desc": c.label or ""
                }

            await update_template_async(
                template_id,
                TemplateUpdate(variables_schema=schema, bindings=bindings)
            )
        else:
            await update_template_async(
                template_id,
                TemplateUpdate(bindings=bindings)
            )

        return compiled_path, compiled_name



    async def detect_blank_fields_llm(
        self, template_id: int, workspace_id: str, min_confidence: float = 0.5
    ) -> List[CandidateField]:
        """
        使用 LLM 智能检测模板空白字段
        
        完整流程：
        1. docx_to_html_with_mapping - 获取 HTML 和初始 mapping
        2. to_dense_html - 清洗 HTML，转译视觉特征，注入 Sub-ID
        3. detect_via_llm_async - LLM 语义分析 + 双重验证
        
        [Design Rigor] 遵循计划书架构，模块解耦
        [Async First] 全程异步
        
        Args:
            template_id: 模板 ID
            workspace_id: 工作空间 ID（权限校验）
            min_confidence: 最小置信度阈值
            
        Returns:
            候选字段列表
        """
        template = await self.get_template(template_id, workspace_id)
        if not template:
            raise ValueError("模板不存在或无权访问")
        
        if template.file_type.lower() != "docx":
            raise ValueError("仅支持 DOCX 模板的 LLM 智能检测")
        
        try:
            # Step 1: DOCX -> HTML + Mapping
            result = await self.docx_to_html_with_mapping(template.file_path)
            raw_html = result["html"]
            raw_mapping = result["mapping"]
            
            # Step 2: Dense HTML 清洗
            dense_html, updated_mapping = self.converter.to_dense_html(raw_html, raw_mapping)
            
            # Step 3: LLM 智能检测
            candidates = await self.detector.detect_via_llm_async(dense_html, updated_mapping)
            
            # 按置信度阈值过滤
            filtered = [c for c in candidates if c.confidence >= min_confidence]
            
            logger.info(
                f"模板 {template_id} LLM 智能检测完成: "
                f"LLM 返回 {len(candidates)}, 过滤后 {len(filtered)}"
            )
            return filtered
            
        except Exception as e:
            logger.error(f"LLM 智能检测失败: {e}")
            raise ValueError(f"LLM 检测失败: {e}")

    # ========== LLM 后台任务 ==========

    async def start_llm_detection_task(
        self, 
        template_id: int, 
        workspace_id: str, 
        user_id: str
    ) -> dict:
        """
        启动 LLM 智能检测后台任务
        
        [Async First] 任务在后台异步执行
        [Design Rigor] 返回 task_id 供前端轮询
        
        Args:
            template_id: 模板 ID
            workspace_id: 工作空间 ID
            user_id: 用户 ID
            
        Returns:
            包含 task_id 和初始状态的字典
        """
        # 校验模板存在性和权限
        template = await self.get_template(template_id, workspace_id)
        if not template:
            raise ValueError("模板不存在或无权访问")
        
        if template.file_type.lower() != "docx":
            raise ValueError("仅支持 DOCX 模板的 LLM 智能检测")
        
        # 创建任务
        task = self.task_manager.create_task(
            template_id=template_id,
            workspace_id=workspace_id,
            user_id=user_id
        )
        
        # 启动后台执行
        self.task_executor.start_background_detection(
            task_id=task.task_id,
            file_path=template.file_path
        )
        
        return {
            "task_id": task.task_id,
            "status": task.status,
            "progress": task.progress,
            "stage": task.stage
        }

    def get_detection_task_status(self, task_id: str) -> dict:
        """
        查询检测任务状态
        
        Args:
            task_id: 任务 ID
            
        Returns:
            任务状态字典
        """
        task = self.task_manager.get_task(task_id)
        if not task:
            raise ValueError("任务不存在或已过期")
        
        return {
            "task_id": task.task_id,
            "status": task.status,
            "progress": task.progress,
            "stage": task.stage,
            "result": task.result,
            "error": task.error
        }

    # ========== 模板分组 ==========

    async def list_template_groups(self, workspace_id: str) -> List[TemplateGroup]:
        """获取模板分组列表"""
        return await list_template_groups_async(workspace_id=workspace_id)

    async def create_template_group(
        self, request: TemplateGroupCreate, workspace_id: str, user_id: str
    ) -> TemplateGroup:
        """创建模板分组"""
        return await create_template_group_async(group=request, workspace_id=workspace_id, user_id=user_id)

    async def update_template_group(
        self, group_id: int, request: TemplateGroupUpdate, workspace_id: str
    ) -> Optional[TemplateGroup]:
        """更新模板分组"""
        existing = await get_template_group_async(group_id)
        if not existing:
            raise ValueError("分组不存在")
        if existing.workspace_id != workspace_id:
            return None
        return await update_template_group_async(group_id, request)

    async def delete_template_group(self, group_id: int, workspace_id: str) -> Tuple[bool, str]:
        """删除模板分组"""
        existing = await get_template_group_async(group_id)
        if not existing:
            return (False, "分组不存在")
        if existing.workspace_id != workspace_id:
            return (False, "无权删除此分组")

        success = await delete_template_group_async(group_id)
        if not success:
            return (False, "删除失败")
        return (True, "分组已删除，组内模板已移至未分组")

    # ========== LLM 命名优化 ==========

    async def optimize_candidates_with_llm(
        self, candidates: List[CandidateField]
    ) -> List[CandidateField]:
        """
        使用 LLM 优化候选字段的标签命名
        
        Args:
            candidates: 候选字段列表
            
        Returns:
            优化后的候选字段列表（原地修改 label）
        """
        if not candidates:
            return candidates
        
        try:
            from app.core.llm.async_llm import get_async_llm
        except ImportError:
            logger.warning("LLM 模块未安装，跳过命名优化")
            return candidates
        
        llm = get_async_llm()
        
        # 准备输入数据
        input_data = [
            {"key": c.key, "label": c.label, "context": c.context or ""}
            for c in candidates
        ]
        
        # Prompt（配置化: 未来可移至配置文件）
        system_prompt = "你是专业的文档模版分析助手，擅长根据上下文优化变量命名。"
        user_prompt = f"""请优化以下候选字段的命名标签。

字段列表：
{input_data}

优化要求：
1. 命名简洁、准确、专业（2-8 字为宜）
2. 使用规范中文，避免口语化
3. 对于 context 为空的字段，保持原 label 不变
4. 避免重复命名，如有冲突请加上下文区分

请返回优化后的标签列表。"""

        # Tool Schema
        tool_schema = {
            "type": "function",
            "function": {
                "name": "submit_optimized_labels",
                "description": "提交优化后的变量标签列表",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "optimized_labels": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "key": {"type": "string", "description": "原始字段 key"},
                                    "label": {"type": "string", "description": "优化后的标签"}
                                },
                                "required": ["key", "label"]
                            }
                        }
                    },
                    "required": ["optimized_labels"]
                }
            }
        }
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        try:
            result = await llm.generate_structured(
                messages=messages,
                tool_schema=tool_schema,
                temperature=0.3  # 低温度保证输出稳定
            )
            
            optimized_map = {
                item["key"]: item["label"]
                for item in result.get("optimized_labels", [])
            }
            
            # 更新候选字段
            for c in candidates:
                if c.key in optimized_map:
                    c.label = optimized_map[c.key]
            
            logger.info(f"LLM 优化完成: {len(optimized_map)} 个标签已更新")
            return candidates
            
        except Exception as e:
            logger.warning(f"LLM 优化失败，保留原标签: {e}")
            return candidates


# 单例
_service: TemplateService = None


def get_template_service() -> TemplateService:
    """获取模板服务单例"""
    global _service
    if _service is None:
        _service = TemplateService()
    return _service
