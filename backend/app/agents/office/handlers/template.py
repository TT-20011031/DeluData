"""
模板渲染处理器 - 确定性渲染（docx/xlsx）
"""
import json
import os
import posixpath
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, Optional

from app.models.config.template import get_template_async
from app.services.template import get_template_renderer, get_template_service
from app.core.storage.service import get_storage_service
from app.core.utils.storage_path import (
    build_oss_path,
    is_oss_path,
    normalize_storage_path,
    parse_oss_path,
)
from app.core.llm.async_llm import get_async_llm
from app.core.llm.prompt_manager import get_prompt
from app.config import get_settings
from app.services.generation_data_guard import evaluate_generation_data_relevance
from app.services.generation_data_service import build_query_focused_context

from .base import TaskHandler, TaskContext

logger = logging.getLogger(__name__)


class TemplateHandler(TaskHandler):
    """
    模板模式：使用已上传模板 + 已准备好的 context 渲染输出
    """

    async def handle(self, ctx: TaskContext) -> Dict[str, Any]:
        logger.info(f"[TemplateHandler] 开始处理, template_id={ctx.template_id}, template_mode={ctx.template_mode}")
        
        template_mode = (ctx.template_mode or "").lower()
        if template_mode in ["draft", "preview"]:
            logger.info("[TemplateHandler] 全局跳过预览，直接渲染")
            template_mode = "render"
        await self.on_step(ctx, "template_render", "running", "正在渲染模板...")

        template_id = ctx.template_id
        if not template_id:
            logger.warning("[TemplateHandler] 缺少 template_id")
            return {"success": False, "error": "缺少 template_id"}

        try:
            logger.info(f"[TemplateHandler] 正在获取模板 id={template_id}")
            template = await get_template_async(int(template_id))
            logger.info(f"[TemplateHandler] 模板获取结果: {template is not None}")
        except Exception as e:
            logger.error(f"TemplateHandler: get_template failed: {e}", exc_info=True)
            return {"success": False, "error": "模板读取失败"}

        if not template:
            logger.warning(f"[TemplateHandler] 模板不存在, id={template_id}")
            return {"success": False, "error": "模板不存在"}

        logger.info(f"[TemplateHandler] 模板信息: name={template.name}, file_type={template.file_type}, file_path={template.file_path}")

        # Workspace 校验（尽力而为）
        if ctx.user_context:
            ws_id = ctx.user_context.get("workspace_id")
            if ws_id and template.workspace_id != ws_id:
                logger.warning(f"[TemplateHandler] 无权限访问, template.workspace_id={template.workspace_id}, user.workspace_id={ws_id}")
                return {"success": False, "error": "模板无权限访问"}

        # 从 memory_dfs 提取 template_context
        template_context = self._extract_template_context(ctx.memory_dfs)
        if not template_context:
            try:
                template_context = await self._generate_template_context(ctx, template)
                logger.info(f"[TemplateHandler] 自动生成 template_context: {template_context is not None}")
            except Exception as e:
                logger.error(f"[TemplateHandler] _generate_template_context 异常: {e}", exc_info=True)
                template_context = None

        if not template_context:
            template_context = template.example_context

        if not template_context:
            return {"success": False, "error": "缺少 template_context，无法渲染模板"}

        template_context = self._normalize_template_context(template, template_context)

        relevance = await evaluate_generation_data_relevance(
            query=ctx.task_description,
            memory_dfs=ctx.memory_dfs,
            execution_results=ctx.execution_results,
            messages=ctx.messages,
        )
        if not relevance.get("is_relevant", True):
            reason = str(relevance.get("reason", "数据与问题不相关"))
            await self.on_step(ctx, "template_render", "invalid_data", "数据无效，已跳过生成")
            return {
                "success": True,
                "output": f"数据无效，已跳过文档生成。原因：{reason}",
                "output_files": [],
                "quality_signal": {
                    "verdict": "pass",
                    "reason_code": "terminal_data_invalid",
                    "confidence": relevance.get("confidence", 0.9),
                    "retryable": False,
                },
                "meta": {
                    "worker_round": ctx.round_index,
                    "stop_reason": "terminal_data_invalid",
                    "step_status": "invalid_data",
                    "invalid_reason": reason,
                },
            }

        if not self._has_meaningful_render_values(template, template_context):
            logger.warning("[TemplateHandler] 模板字段未提取到有效值, template_id=%s", template.id)
            await self.on_step(ctx, "template_render", "error", "模板字段未提取到有效值")
            return {
                "success": False,
                "error": "模板字段未提取到有效值，已阻止生成空白文档",
                "quality_signal": {
                    "verdict": "fail",
                    "reason_code": "template_context_empty",
                    "confidence": 0.95,
                    "retryable": True,
                },
                "meta": {
                    "worker_round": ctx.round_index,
                    "stop_reason": "template_context_empty",
                },
            }

        # 输出文件名
        filename = ctx.output_filename or template.name or f"template_{template.id}"
        ext = f".{template.file_type}"
        if not filename.lower().endswith(ext):
            filename += ext

        Path(ctx.sandbox_path).mkdir(parents=True, exist_ok=True)
        output_path = os.path.join(ctx.sandbox_path, filename)

        try:
            async with self._materialized_render_template(template) as render_template:
                renderer = get_template_renderer()
                await renderer.render_to_path(render_template, template_context, output_path)
        except Exception as e:
            logger.error(f"TemplateHandler: render failed: {e}", exc_info=True)
            await self.on_step(ctx, "template_render", "error", "渲染失败")
            return {
                "success": False,
                "error": f"模板渲染失败: {str(e)}",
                "quality_signal": {
                    "verdict": "fail",
                    "reason_code": "doc_generation_error",
                    "confidence": 0.9,
                    "retryable": True,
                },
                "meta": {
                    "worker_round": ctx.round_index,
                    "stop_reason": "doc_generation_error",
                },
            }

        download_url = f"/api/files/download/{ctx.session_id}/{filename}"
        await self.on_step(ctx, "template_render", "done", "渲染完成")

        # 保存到临时产物收纳箱（按 workspace_id + user_id 隔离）
        try:
            from app.services.temp_artifact_service import get_temp_artifact_service
            user_ctx = ctx.user_context or {}
            workspace_id = str(user_ctx.get("workspace_id") or "default")
            user_id = str(user_ctx.get("user_id") or ctx.user_id or "anonymous")
            await get_temp_artifact_service().save_doc(
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=ctx.session_id or "",
                title=filename[:50],
                download_url=download_url,
                file_name=filename,
                file_kind="word" if template.file_type == "docx" else "excel",
            )
        except Exception as save_err:
            logger.warning("TemplateHandler: 暂存箱保存失败(非致命): %s", save_err)

        if ctx.session_id:
            from app.api.events import emit_file_result
            file_type = "word" if template.file_type == "docx" else "excel"
            await emit_file_result(
                session_id=ctx.session_id,
                step_id=ctx.parent_step_id,
                file_type=file_type,
                file_name=filename,
                download_url=download_url,
                round_index=ctx.round_index
            )

        return {
            "success": True,
            "output": f"已成功生成文档 [{filename}]({download_url})。",
            "output_files": [{"name": filename, "path": output_path, "download_url": download_url}],
            "quality_signal": {
                "verdict": "pass",
                "reason_code": "doc_generated",
                "confidence": 0.9,
                "retryable": False,
            },
            "meta": {
                "worker_round": ctx.round_index,
                "stop_reason": "doc_generated",
            },
        }

    @asynccontextmanager
    async def _materialized_render_template(self, template):
        storage_service = get_storage_service()
        template_path = template.file_path

        if template.file_type == "docx" and template.id is not None:
            compiled_path = self._build_compiled_template_path(template.file_path, template.id)
            if await storage_service.exists(compiled_path):
                template_path = compiled_path
                logger.info("[TemplateHandler] 使用编译模板: %s", compiled_path)
            elif getattr(template, "bindings", None):
                compiled_path, _ = await get_template_service().compile_template(
                    template,
                    template.bindings or [],
                )
                template_path = compiled_path
                logger.info("[TemplateHandler] 已即时编译模板: %s", compiled_path)

        suffix = os.path.splitext(template_path or "")[1] or f".{template.file_type}"
        filename = os.path.basename(template_path)
        async with storage_service.materialize(
            template_path,
            suffix=suffix,
            filename=filename,
        ) as local_template_path:
            yield template.model_copy(update={"file_path": local_template_path})

    def _build_compiled_template_path(self, file_path: str, template_id: int) -> str:
        compiled_name = f"compiled_{template_id}.docx"
        if is_oss_path(file_path):
            bucket, key = parse_oss_path(file_path)
            key_dir = posixpath.dirname(key)
            return build_oss_path(bucket, posixpath.join(key_dir, compiled_name))

        base_dir = os.path.dirname(file_path)
        return normalize_storage_path(os.path.join(base_dir, compiled_name))

    def _extract_template_context(self, memory_dfs: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not memory_dfs or not isinstance(memory_dfs, dict):
            return None

        ctx = memory_dfs.get("template_context") or memory_dfs.get("template_ctx")
        if ctx is None:
            return None

        if isinstance(ctx, dict):
            return ctx

        if isinstance(ctx, str):
            try:
                parsed = json.loads(ctx)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return None

        # 兼容带 step_id 前缀的 key
        for key, value in memory_dfs.items():
            if isinstance(key, str) and "template_context" in key:
                if isinstance(value, dict):
                    return value
                if isinstance(value, str):
                    try:
                        parsed = json.loads(value)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        continue

        return None

    async def _generate_template_context(self, ctx: TaskContext, template) -> Dict[str, Any]:
        schema = self._get_render_schema(template)
        example_context = self._build_render_example_context(template, schema=schema)

        data_context = build_query_focused_context(
            query=ctx.task_description,
            memory_dfs=ctx.memory_dfs,
            max_sources=3,
            max_rows=15,
            max_chars=4500,
            max_chars_per_source=1600,
            context_title="筛选后的可用数据（仅保留与模板填充直接相关的内容）",
        )

        schema_json = json.dumps(schema, ensure_ascii=False)
        example_json = json.dumps(example_context, ensure_ascii=False) if example_context else ""

        system_prompt = get_prompt("template_context_system", module="office")
        user_prompt = get_prompt(
            "template_context_user",
            module="office",
            task_description=ctx.task_description,
            template_schema=schema_json,
            example_context=example_json,
            data_context=data_context
        )

        llm = get_async_llm()
        settings = get_settings()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            raw = await llm.chat(messages, model=settings.llm.office_worker_model)
            parsed = llm._parse_json_from_text(raw) if hasattr(llm, "_parse_json_from_text") else json.loads(raw)
        except Exception as e:
            logger.warning(f"TemplateHandler: 解析模板上下文失败: {e}")
            parsed = {}

        if not isinstance(parsed, dict):
            parsed = {}

        if isinstance(schema, dict) and schema:
            filtered: Dict[str, Any] = {}
            for key, meta in schema.items():
                field_type = (meta or {}).get("type")
                if key in parsed:
                    filtered[key] = parsed.get(key)
                elif key in example_context:
                    filtered[key] = example_context.get(key)
                else:
                    if field_type == "table":
                        filtered[key] = []
                    else:
                        filtered[key] = ""
            return self._normalize_template_context(template, filtered, render_schema=schema)

        return self._normalize_template_context(template, parsed or example_context, render_schema=schema)

    def _parse_schema_dict(self, raw_schema: Any) -> Dict[str, Any]:
        if isinstance(raw_schema, dict):
            return raw_schema
        if isinstance(raw_schema, str):
            try:
                parsed = json.loads(raw_schema)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    def _parse_context_dict(self, raw_context: Any) -> Dict[str, Any]:
        if isinstance(raw_context, dict):
            return raw_context
        if isinstance(raw_context, str):
            try:
                parsed = json.loads(raw_context)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    def _get_render_schema(self, template) -> Dict[str, Any]:
        schema = self._parse_schema_dict(template.variables_schema)
        bindings = getattr(template, "bindings", None) or []
        if not bindings:
            return schema

        render_schema: Dict[str, Any] = {}
        for binding in bindings:
            key = binding.get("key") or binding.get("var") or binding.get("name")
            if not key:
                continue
            meta = schema.get(key, {}) if isinstance(schema, dict) else {}
            render_schema[key] = {
                "type": binding.get("type") or meta.get("type") or "text",
                "desc": binding.get("label") or meta.get("desc") or meta.get("label") or key,
            }

        return render_schema or schema

    def _build_render_example_context(self, template, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        render_schema = schema or self._get_render_schema(template)
        example_context = self._parse_context_dict(template.example_context)
        return self._normalize_template_context(
            template,
            example_context,
            render_schema=render_schema,
        )

    def _normalize_template_context(
        self,
        template,
        context: Optional[Dict[str, Any]],
        *,
        render_schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raw_context = context if isinstance(context, dict) else {}
        schema = render_schema or self._get_render_schema(template)
        if not schema:
            return raw_context

        full_schema = self._parse_schema_dict(template.variables_schema)
        normalized: Dict[str, Any] = {}
        for key, meta in schema.items():
            value = raw_context.get(key)
            if self._is_meaningful_value(value):
                normalized[key] = value
                continue

            alias_value = None
            for alias in self._iter_context_aliases(key, template, full_schema):
                if alias == key:
                    continue
                candidate = raw_context.get(alias)
                if self._is_meaningful_value(candidate):
                    alias_value = candidate
                    break

            if alias_value is not None:
                normalized[key] = alias_value
                continue

            if key in raw_context:
                normalized[key] = raw_context.get(key)
            elif (meta or {}).get("type") == "table":
                normalized[key] = []
            else:
                normalized[key] = ""

        return normalized

    def _iter_context_aliases(self, key: str, template, full_schema: Dict[str, Any]):
        aliases = []
        bindings = getattr(template, "bindings", None) or []
        for binding in bindings:
            binding_key = binding.get("key") or binding.get("var") or binding.get("name")
            if binding_key != key:
                continue
            aliases.extend([
                binding.get("label"),
                binding.get("name"),
                binding.get("var"),
            ])

        meta = full_schema.get(key, {}) if isinstance(full_schema, dict) else {}
        aliases.extend([meta.get("desc"), meta.get("label")])

        for alias_key, alias_meta in (full_schema or {}).items():
            if alias_key == key or not isinstance(alias_meta, dict):
                continue
            alias_desc = alias_meta.get("desc") or alias_meta.get("label")
            if alias_key == key or alias_key in aliases:
                aliases.append(alias_key)
                continue
            if alias_key == meta.get("desc") or alias_key == meta.get("label"):
                aliases.append(alias_key)
                continue
            if alias_desc and alias_desc in aliases:
                aliases.append(alias_key)

        seen = set()
        for alias in aliases:
            if not isinstance(alias, str):
                continue
            clean_alias = alias.strip().rstrip(":：")
            if not clean_alias or clean_alias in seen:
                continue
            seen.add(clean_alias)
            yield clean_alias

    def _has_meaningful_render_values(self, template, context: Dict[str, Any]) -> bool:
        render_schema = self._get_render_schema(template)
        if not render_schema:
            return any(self._is_meaningful_value(value) for value in context.values())
        return any(
            self._is_meaningful_value(context.get(key))
            for key in render_schema
        )

    def _is_meaningful_value(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list):
            return len(value) > 0
        return True

    def _build_preview_payload(self, template, context: Dict[str, Any]) -> Dict[str, Any]:
        schema = self._parse_schema_dict(template.variables_schema)
        fields = []
        tables = []

        for key, value in context.items():
            meta = schema.get(key, {}) if isinstance(schema, dict) else {}
            field_type = meta.get("type")
            if not field_type:
                field_type = "table" if isinstance(value, list) else "text"
            label = meta.get("desc") or meta.get("label") or key

            if field_type == "table" and isinstance(value, list):
                tables.append({
                    "key": key,
                    "label": label,
                    "rows": value,
                    "columns": meta.get("columns")
                })
            else:
                fields.append({
                    "key": key,
                    "label": label,
                    "type": field_type,
                    "value": value
                })

        return {
            "template_id": template.id,
            "template_name": template.name,
            "file_type": template.file_type,
            "schema": schema,
            "context": context,
            "fields": fields,
            "tables": tables
        }
