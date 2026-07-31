"""
DeluData 智能问数系统 - XiYan Skill 模块

析言 GBI (XiYan) NL2SQL 技能封装
使用阿里云官方 OpenAPI SDK 处理签名
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Callable, Any

from alibabacloud_tea_openapi_sse.client import Client as OpenApiClient
from alibabacloud_tea_openapi_sse import models as open_api_models
from alibabacloud_tea_util import models as util_models

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class XiYanStep:
    """XiYan 处理步骤"""
    name: str           # 问题改写 / 数据表选取 / SQL生成 / 执行SQL
    status: str         # pending / running / done / error
    detail: str = ""    # 具体内容


@dataclass
class XiYanResult:
    """XiYan 返回结果"""
    success: bool
    sql: str = ""
    rewrite: str = ""                          # 改写后的问题
    selected_tables: List[str] = field(default_factory=list)  # 选中的表
    steps: List[XiYanStep] = field(default_factory=list)      # 处理步骤
    error: str = ""


class XiYanSkill:
    """
    析言 GBI NL2SQL 技能
    
    使用阿里云官方 Tea OpenAPI SSE SDK 实现
    SDK 自动处理签名逻辑，避免 SignatureDoesNotMatch 错误
    
    设置环境变量 XIYAN_MOCK=true 可启用模拟模式，避免消耗真实 API 调用次数
    """
    
    def __init__(self):
        settings = get_settings()
        self.config = settings.xiyan
        self._client: Optional[OpenApiClient] = None
        self._mock_mode = os.getenv("XIYAN_MOCK", "false").lower() == "true"
        if self._mock_mode:
            logger.info("🎭 XiYan 模拟模式已启用")
        self._validate_config()
    
    def _validate_config(self):
        """验证配置"""
        if not self.config.is_configured:
            logger.warning("XiYan 未配置 ACCESS_KEY_ID 或 ACCESS_KEY_SECRET")
    
    def _get_client(self) -> OpenApiClient:
        """获取 OpenAPI 客户端 (懒加载)"""
        if self._client is None:
            config = open_api_models.Config(
                access_key_id=self.config.access_key_id,
                access_key_secret=self.config.access_key_secret
            )
            config.endpoint = self.config.endpoint
            self._client = OpenApiClient(config)
        return self._client
    
    async def generate_sql(
        self, 
        question: str,
        step_callback: Optional[Callable[[str, str, str], Any]] = None
    ) -> XiYanResult:
        """
        生成 SQL
        
        Args:
            question: 用户问题
            step_callback: 步骤回调，用于实时推送进度
                          签名: async def callback(name: str, status: str, detail: str)
                          
        Returns:
            XiYanResult 包含 SQL 和步骤信息
        """
        # 🎭 模拟模式
        if self._mock_mode:
            return await self._generate_sql_mock(question, step_callback)
        
        if not self.config.is_configured:
            return XiYanResult(
                success=False,
                error="XiYan 未配置，请设置 XIYAN_ACCESS_KEY_ID 和 XIYAN_ACCESS_KEY_SECRET 环境变量"
            )
        
        # 增强问题
        enhanced_question = self._enhance_question(question)
        
        steps = []
        result = XiYanResult(success=False)
        
        try:
            # 调用回调：开始处理
            if step_callback:
                await self._safe_callback(step_callback, "问题改写", "running", "分析用户问题...")
            
            # 使用 SDK 发起请求
            logger.info("XiYan SDK: 开始调用 RunSqlGeneration")
            
            # 直接调用异步 SDK 方法
            api_result = await self._call_sdk(enhanced_question)
            
            logger.info(f"XiYan SDK: 收到响应")
            
            # 处理响应
            if not api_result:
                error_msg = "XiYan SDK 未返回任何有效的流式事件 (SSE)"
                result.error = error_msg
                logger.error(error_msg)
                if step_callback:
                    await self._safe_callback(step_callback, "SQL生成", "error", error_msg)
            else:
                for i, (event_type, event_data) in enumerate(api_result):
                    logger.info(f"XiYan SSE 事件 [{i}]: {event_type}")
                    
                    if event_type == "rewrite":
                        rewrite = event_data.get("rewrite", question)
                        result.rewrite = rewrite
                        steps.append(XiYanStep("问题改写", "done", rewrite))
                        if step_callback:
                            await self._safe_callback(step_callback, "问题改写", "done", rewrite)
                            await self._safe_callback(step_callback, "数据表选取", "running", "分析相关数据表...")
                    
                    elif event_type == "selector":
                        tables = event_data.get("selector", [])
                        if isinstance(tables, list):
                            result.selected_tables = [str(t) for t in tables]
                        table_names = ", ".join(result.selected_tables) if result.selected_tables else "无"
                        steps.append(XiYanStep("数据表选取", "done", table_names))
                        if step_callback:
                            await self._safe_callback(step_callback, "数据表选取", "done", table_names)
                            await self._safe_callback(step_callback, "SQL生成", "running", "生成 SQL 语句...")
                    
                    elif event_type == "sql":
                        sql = event_data.get("sql", "")
                        result.sql = sql
                        result.success = True
                        steps.append(XiYanStep("SQL生成", "done", sql[:100] + "..." if len(sql) > 100 else sql))
                        if step_callback:
                            await self._safe_callback(step_callback, "SQL生成", "done", sql)
                    
                    elif event_type == "error":
                        error_msg = event_data.get("errorMessage", "业务逻辑错误")
                        result.error = error_msg
                        steps.append(XiYanStep("错误", "error", error_msg))
                        if step_callback:
                            await self._safe_callback(step_callback, "SQL生成", "error", error_msg)
                
                # 检查是否成功
                if not result.success and not result.error:
                    error_msg = "XiYan GBI 响应中未找到 SQL 生成结果"
                    result.error = error_msg
                    if step_callback:
                        await self._safe_callback(step_callback, "SQL生成", "error", error_msg)
            
            result.steps = steps
            
        except Exception as e:
            logger.error(f"XiYan SDK 调用失败: {e}")
            result.error = str(e)
            result.steps = steps
            if step_callback:
                await self._safe_callback(step_callback, "SQL生成", "error", str(e))
        
        return result
    
    async def _call_sdk(self, question: str) -> List[tuple]:
        """
        使用 SDK 调用 XiYan API (异步)
        
        修复说明：
        - action: RunDataAnalysis (不是 RunSqlGeneration)
        - pathname: /{workspace_id}/gbi/runDataAnalysis
        - style: RPC (不是 ROA)
        - body_type: sse (不是 json)
        
        Returns:
            [(event_type, event_data), ...] 事件列表
        """
        client = self._get_client()
        
        # ⭐ 正确的 API 参数 (来自参考项目)
        params = open_api_models.Params(
            action='RunDataAnalysis',          # 关键修复
            version='2024-08-23',
            protocol='HTTPS',
            method='POST',
            auth_type='AK',
            style='RPC',                        # 关键修复：RPC 而不是 ROA
            pathname=f'/{self.config.workspace_id}/gbi/runDataAnalysis',  # 关键修复
            req_body_type='json',
            body_type='sse'                     # 关键修复：sse 而不是 json
        )
        
        # RunDataAnalysis only requires the purchased specification and query.
        body = {
            "specificationType": self.config.specification_type,
            "query": question,
        }
        
        # 运行时选项
        runtime = util_models.RuntimeOptions()
        runtime.read_timeout = 100000  # 100秒超时
        runtime.connect_timeout = 10000
        
        # 构建请求 - 不再需要 query 参数
        request = open_api_models.OpenApiRequest(body=body)
        
        events = []
        
        try:
            # 调用 SSE API (异步方法)
            sse_receiver = client.call_sse_api_async(
                params=params, 
                request=request, 
                runtime=runtime
            )
            
            # ⭐ 修复事件解析逻辑 (来自参考项目)
            async for res in sse_receiver:
                try:
                    # 参考项目的解析方式: res.get('event').data
                    if isinstance(res, dict):
                        event_obj = res.get('event')
                        if event_obj is not None and hasattr(event_obj, 'data') and event_obj.data:
                            raw_data = event_obj.data
                            data = json.loads(raw_data)
                            inner_data = data.get('data', {})
                            event_type = inner_data.get('event', 'unknown')
                            events.append((event_type, inner_data))
                            logger.info(f"XiYan SSE 事件: {event_type}")
                            
                            # 收到 sql 事件就停止
                            if event_type == 'sql':
                                break
                            elif event_type == 'error':
                                break
                    # 兼容旧方式
                    elif hasattr(res, 'data') and res.data:
                        data = json.loads(res.data)
                        event_type = data.get('event', data.get('data', {}).get('event', 'unknown'))
                        events.append((event_type, data))
                        logger.info(f"XiYan SSE 事件 (旧格式): {event_type}")
                        if event_type == 'sql':
                            break
                            
                except json.JSONDecodeError as e:
                    logger.warning(f"无法解析 SSE 数据: {e}")
                except Exception as e:
                    logger.warning(f"SSE 事件处理异常: {e}")
                        
        except Exception as e:
            logger.error(f"SDK API 调用失败: {e}")
            events.append(("error", {"errorMessage": str(e)}))
        
        return events
    
    async def _safe_callback(self, callback, name: str, status: str, detail: str):
        """安全调用回调"""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(name, status, detail)
            else:
                callback(name, status, detail)
        except Exception as e:
            logger.error(f"步骤回调执行失败: {e}")
    
    async def _generate_sql_mock(
        self,
        question: str,
        step_callback: Optional[Callable[[str, str, str], Any]] = None
    ) -> XiYanResult:
        """🎭 模拟模式 - 委托给独立模块"""
        from app.skills.xiyan_mock import generate_sql_mock
        return await generate_sql_mock(question, step_callback, self._safe_callback)
    
    def _enhance_question(self, question: str) -> str:
        """
        增强问题
        
        添加日期约束和格式要求
        """
        today = datetime.now().strftime("%Y-%m-%d")
        
        enhancement = f"""
【重要约束 - 必须遵守】
1. 所有日期/时间类型字段必须在 SELECT 中转换为字符串格式：
   - DATE_FORMAT(日期字段, '%Y-%m-%d') AS 日期字段
   - DATE_FORMAT(时间字段, '%Y-%m-%d %H:%i:%s') AS 时间字段
2. 如果查询涉及"最近"但未指定时间范围，默认使用最近一个月
3. 当前日期: {today}

用户问题: {question}
"""
        return enhancement.strip()


# 单例实例
_xiyan_skill: Optional[XiYanSkill] = None


def get_xiyan_skill() -> XiYanSkill:
    """获取 XiYan Skill 单例"""
    global _xiyan_skill
    if _xiyan_skill is None:
        _xiyan_skill = XiYanSkill()
    return _xiyan_skill
