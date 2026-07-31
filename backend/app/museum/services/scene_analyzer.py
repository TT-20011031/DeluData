"""
博物馆模块 - VLM 场景分析服务

遵循设计原则：
- VLM 端到端决策：废弃硬编码权重，由大模型综合判断
- 配置外置原则 (No Hardcoding)
- 全异步 I/O (Async First)

功能：
- 多人检测与群体关系分析
- 消费力/专注度/影响力三维评分
- 智能策略建议生成
"""
import asyncio
import json
import logging
import base64
from typing import Optional, List
from pathlib import Path
from dataclasses import dataclass

from app.museum.config import get_museum_settings

logger = logging.getLogger(__name__)


# ========== 数据模型 ==========

@dataclass
class PersonScores:
    """人物三维评分"""
    purchasing_power: float  # 消费能力 (0-10)
    engagement: float        # 专注度 (0-10)
    influence: float         # 影响力 (0-10)


@dataclass
class PersonInsight:
    """单个人物的高维分析结果"""
    id: str
    bbox: List[float]        # [x1, y1, x2, y2] 归一化坐标
    role_label: str          # 显性标签
    visual_cues: List[str]   # 视觉线索
    scores: PersonScores     # 三维评分
    final_weight: float      # 综合权重 (0-100)
    ethnicity: str = "unknown"  # 人种: asian/caucasian/african/middle_eastern/latino/unknown


@dataclass
class SceneAnalysisResult:
    """整体场景分析结果"""
    total_count: int
    group_dynamic: str              # 群体关系
    persons: List[PersonInsight]
    target_person_id: str           # 建议主要对话对象
    suggested_tone: str             # 建议语气
    engagement_strategy: str        # 切入策略
    analysis_steps: List[dict]      # 分析过程步骤
    primary_ethnicity: str = "asian"    # 群体主要人种
    suggested_language: str = "zh"      # 建议服务语言: zh/en/es


# ========== VLM Prompt ==========

SCENE_ANALYSIS_PROMPT = """你是一个专业的博物馆导览顾问。请分析图片中的游客。

## 核心任务
1. **数人数**：图片中有几个人？假设有6个人，则 total_count=6，persons数组必须有6个元素。
2. **逐个分析**：为每个人单独创建一个 person 对象，不要把多人合并成一个。
3. **识别人种**：判断每个人的人种/民族特征，用于选择合适的语言服务。
4. **给出策略**：具体的开场白，不要用模板。

## JSON 格式（严格遵守）
```json
{
  "total_count": 6,
  "group_dynamic": "朋友聚会",
  "primary_ethnicity": "asian",
  "suggested_language": "zh",
  "persons": [
    {"id": "p1", "bbox": [0.0, 0.1, 0.16, 0.9], "role_label": "中心人物", "ethnicity": "asian", "visual_cues": ["蓝色T恤", "站C位"], "scores": {"purchasing_power": 6, "engagement": 8, "influence": 9}, "final_weight": 85},
    {"id": "p2", "bbox": [0.16, 0.1, 0.32, 0.9], "role_label": "成员", "ethnicity": "asian", "visual_cues": ["白衬衫"], "scores": {"purchasing_power": 5, "engagement": 7, "influence": 5}, "final_weight": 55},
    {"id": "p3", "bbox": [0.32, 0.1, 0.48, 0.9], "role_label": "成员", "ethnicity": "caucasian", "visual_cues": ["黄色上衣"], "scores": {"purchasing_power": 5, "engagement": 7, "influence": 5}, "final_weight": 55},
    {"id": "p4", "bbox": [0.48, 0.1, 0.64, 0.9], "role_label": "成员", "ethnicity": "asian", "visual_cues": ["牛仔裤"], "scores": {"purchasing_power": 5, "engagement": 7, "influence": 5}, "final_weight": 55},
    {"id": "p5", "bbox": [0.64, 0.1, 0.80, 0.9], "role_label": "成员", "ethnicity": "african", "visual_cues": ["连衣裙"], "scores": {"purchasing_power": 6, "engagement": 7, "influence": 5}, "final_weight": 58},
    {"id": "p6", "bbox": [0.80, 0.1, 1.0, 0.9], "role_label": "成员", "ethnicity": "asian", "visual_cues": ["休闲装"], "scores": {"purchasing_power": 5, "engagement": 7, "influence": 5}, "final_weight": 55}
  ],
  "target_person_id": "p1",
  "suggested_tone": "亲切活泼",
  "engagement_strategy": "您好！看大家玩得很开心，我来给大家讲个这件展品背后的有趣故事吧？"
}
```

## 人种分类 (ethnicity)
- **asian**: 亚洲人 → 中文服务
- **caucasian**: 欧美白人 → 英文服务  
- **african**: 非洲裔 → 英文服务
- **middle_eastern**: 中东人 → 英文服务
- **latino**: 拉丁裔 → 西班牙语/英文服务
- **unknown**: 无法判断 → 中文服务

## 语言建议 (suggested_language)
根据群体中主要人种 (primary_ethnicity) 给出建议语言：
- zh: 中文
- en: 英文
- es: 西班牙语

## 禁止事项
- ❌ 禁止把多人合并成一个 person
- ❌ 禁止 engagement_strategy 使用"根据XX特点，采用XX服务"这种模板
- ❌ 禁止 total_count 与 persons 数组长度不一致
"""


class SceneAnalyzer:
    """
    VLM 场景分析服务
    
    使用大模型端到端分析图片中的游客群体，输出：
    - 人物识别与群体关系
    - 三维评分（消费力、专注度、影响力）
    - 智能服务策略建议
    """
    
    def __init__(self):
        self.settings = get_museum_settings()
        self._client = None
    
    @property
    def client(self):
        """懒加载 DashScope 客户端"""
        if self._client is None:
            try:
                from dashscope import MultiModalConversation
                self._client = MultiModalConversation
            except ImportError:
                logger.error("[SceneAnalyzer] dashscope 未安装")
                raise ImportError("请安装 dashscope: pip install dashscope")
        return self._client
    
    async def analyze(
        self, 
        image_path: str,
        emit_step: Optional[callable] = None
    ) -> SceneAnalysisResult:
        """
        分析场景中的人物
        
        Args:
            image_path: 图片路径或 URL
            emit_step: 进度回调函数 (step_id, title, description, status)
            
        Returns:
            SceneAnalysisResult: 完整分析结果
        """
        analysis_steps = []
        
        async def add_step(step_id: str, title: str, desc: str, status: str = "done"):
            step = {"step_id": step_id, "title": title, "description": desc, "status": status}
            analysis_steps.append(step)
            if emit_step:
                await emit_step(step)
        
        try:
            # Step 1: 准备图片
            await add_step("prepare", "准备图片", "正在处理上传的图片...", "running")
            image_content = await self._prepare_image(image_path)
            await add_step("prepare", "准备图片", "图片处理完成", "done")
            
            # Step 2: VLM 分析
            await add_step("vlm_analyze", "智能识别", "正在分析人物特征与群体关系...", "running")
            
            result = await asyncio.wait_for(
                self._call_vlm(image_content),
                timeout=self.settings.vlm_timeout + 5  # 比单人识别多给一些时间
            )
            
            await add_step("vlm_analyze", "智能识别", f"识别到 {result.total_count} 人", "done")
            
            # Step 3: 策略生成
            await add_step("strategy", "策略生成", "正在制定服务策略...", "running")
            await asyncio.sleep(0.2)  # 短暂延迟，让前端有时间展示
            await add_step("strategy", "策略生成", result.engagement_strategy[:30] + "...", "done")
            
            result.analysis_steps = analysis_steps
            return result
            
        except asyncio.TimeoutError:
            logger.warning(f"[SceneAnalyzer] VLM 超时")
            await add_step("error", "分析超时", "识别超时，已切换为通用模式", "done")
            return self._fallback_result(analysis_steps)
            
        except Exception as e:
            logger.error(f"[SceneAnalyzer] 分析失败: {e}")
            await add_step("error", "分析失败", str(e)[:50], "done")
            return self._fallback_result(analysis_steps)
    
    async def _prepare_image(self, image_path: str) -> dict:
        """准备图片内容"""
        if image_path.startswith(('http://', 'https://')):
            return {"image": image_path}
        
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"图片文件不存在: {image_path}")
        
        image_bytes = await asyncio.to_thread(path.read_bytes)
        base64_data = base64.b64encode(image_bytes).decode()
        
        ext = path.suffix.lower()
        mime_map = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
        }
        mime_type = mime_map.get(ext, 'image/jpeg')
        
        return {"image": f"data:{mime_type};base64,{base64_data}"}
    
    async def _call_vlm(self, image_content: dict) -> SceneAnalysisResult:
        """调用 VLM 进行分析"""
        messages = [
            {
                "role": "user",
                "content": [
                    image_content,
                    {"text": SCENE_ANALYSIS_PROMPT}
                ]
            }
        ]
        
        response = await asyncio.to_thread(
            self.client.call,
            model=self.settings.vlm_model,
            messages=messages,
            max_tokens=2000,  # 需要更多 token 来输出完整 JSON
        )
        
        return self._parse_response(response)
    
    def _parse_response(self, response) -> SceneAnalysisResult:
        """解析 VLM 响应"""
        try:
            if response.status_code != 200:
                raise ValueError(f"API 调用失败: {response.message}")
            
            content = response.output.choices[0].message.content
            
            # 提取文本
            if isinstance(content, list):
                text = None
                for item in content:
                    if isinstance(item, dict) and 'text' in item:
                        text = item['text']
                        break
                if not text:
                    raise ValueError("响应中未找到文本内容")
            else:
                text = str(content)
            
            logger.info(f"[SceneAnalyzer] VLM 原始响应: {text[:500]}...")
            
            # 提取 JSON
            json_start = text.find('{')
            json_end = text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_str = text[json_start:json_end]
                data = json.loads(json_str)
                logger.info(f"[SceneAnalyzer] 解析结果: total_count={data.get('total_count')}, persons数量={len(data.get('persons', []))}")
                return self._build_result(data)
            
            raise ValueError("响应中未找到有效 JSON")
            
        except json.JSONDecodeError as e:
            logger.warning(f"[SceneAnalyzer] JSON 解析失败: {e}")
            raise
        except Exception as e:
            logger.error(f"[SceneAnalyzer] 响应解析失败: {e}")
            raise
    
    def _build_result(self, data: dict) -> SceneAnalysisResult:
        """从 JSON 数据构建结果对象"""
        persons = []
        for p in data.get("persons", []):
            scores = p.get("scores", {})
            persons.append(PersonInsight(
                id=p.get("id", "p1"),
                bbox=p.get("bbox", [0, 0, 1, 1]),
                role_label=p.get("role_label", "访客"),
                visual_cues=p.get("visual_cues", []),
                scores=PersonScores(
                    purchasing_power=float(scores.get("purchasing_power", 5)),
                    engagement=float(scores.get("engagement", 5)),
                    influence=float(scores.get("influence", 5)),
                ),
                final_weight=float(p.get("final_weight", 50)),
                ethnicity=p.get("ethnicity", "unknown"),
            ))
        
        # 如果没有人物，添加默认
        if not persons:
            persons.append(PersonInsight(
                id="p1",
                bbox=[0, 0, 1, 1],
                role_label="通用访客",
                visual_cues=[],
                scores=PersonScores(5, 5, 5),
                final_weight=50,
            ))
        
        return SceneAnalysisResult(
            total_count=data.get("total_count", len(persons)),
            group_dynamic=data.get("group_dynamic", "散客"),
            persons=persons,
            target_person_id=data.get("target_person_id", persons[0].id),
            suggested_tone=data.get("suggested_tone", "亲切友好"),
            engagement_strategy=data.get("engagement_strategy", "主动询问是否需要导览服务。"),
            analysis_steps=[],
            primary_ethnicity=data.get("primary_ethnicity", "asian"),
            suggested_language=data.get("suggested_language", "zh"),
        )
    
    def _fallback_result(self, steps: List[dict]) -> SceneAnalysisResult:
        """降级结果"""
        return SceneAnalysisResult(
            total_count=1,
            group_dynamic="散客",
            persons=[PersonInsight(
                id="p1",
                bbox=[0, 0, 1, 1],
                role_label="通用访客",
                visual_cues=["识别超时或失败"],
                scores=PersonScores(5, 9.5, 5),
                final_weight=50,
            )],
            target_person_id="p1",
            suggested_tone="亲切友好",
            engagement_strategy="您好！欢迎参观，请问对哪件展品比较感兴趣？我可以为您详细介绍。",
            analysis_steps=steps,
        )
    
    def get_target_person(self, result: SceneAnalysisResult) -> PersonInsight:
        """获取目标服务对象"""
        for p in result.persons:
            if p.id == result.target_person_id:
                return p
        return result.persons[0] if result.persons else None
    
    def get_tone_for_target(self, result: SceneAnalysisResult) -> str:
        """获取针对目标的语气类型（用于 TTS 音色选择）"""
        tone = result.suggested_tone.lower()
        
        if any(k in tone for k in ["专业", "严谨", "商务", "精简"]):
            return "商务人士"
        elif any(k in tone for k in ["活泼", "童趣", "有趣"]):
            return "儿童"
        elif any(k in tone for k in ["耐心", "细致", "缓慢"]):
            return "老年人"
        elif any(k in tone for k in ["温柔", "优雅", "亲切"]):
            return "妇女"
        else:
            return "通用访客"


# ========== 单例工厂 ==========

_scene_analyzer: Optional[SceneAnalyzer] = None


def get_scene_analyzer() -> SceneAnalyzer:
    """获取场景分析服务单例"""
    global _scene_analyzer
    if _scene_analyzer is None:
        _scene_analyzer = SceneAnalyzer()
    return _scene_analyzer
