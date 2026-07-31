"""
博物馆模块配置

遵循设计原则：配置外置原则 (No Hardcoding)
所有配置从环境变量读取
"""
import os
from functools import lru_cache
from typing import Optional, Dict
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.voice.config import TTSConfig


# 找到 .env 文件路径
def get_env_file() -> Optional[Path]:
    """查找 .env 文件"""
    current = Path(__file__).parent
    for _ in range(5):
        env_file = current / ".env"
        if env_file.exists():
            return env_file
        current = current.parent
    return None


ENV_FILE = get_env_file()


class MuseumSettings(BaseSettings):
    """博物馆模块配置"""
    
    # ========== 火山引擎 TTS 配置 ==========
    volcano_app_id: str = ""  # 从火山引擎控制台获取的 APP ID
    volcano_access_key: str = ""  # 从火山引擎控制台获取的 Access Token
    
    # TTS API 类型：realtime_dialogue 或 bidirection_tts
    volcano_tts_api_type: str = Field(
        default_factory=lambda: (
            os.getenv("MUSEUM_TTS_API_TYPE")
            or os.getenv("MUSEUM_VOLCANO_TTS_API_TYPE")
            or "realtime_dialogue"
        )
    )
    
    # 实时对话 API 配置
    volcano_realtime_url: str = "wss://openspeech.bytedance.com/api/v1/realtime_dialogue"
    volcano_realtime_resource_id: str = "volc.speech.dialog"
    
    # 普通 TTS API 配置
    volcano_tts_url: str = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
    volcano_tts_resource_id: str = "seed-tts-1.0"
    
    # API 固定值（火山引擎文档规定，不可修改）
    volcano_app_key: str = "4R29PBjTFRiv2bNUd5p_2IzGWx56Kpyf"
    
    # ========== VLM 人物识别配置 ==========
    vlm_model: str = "qwen-vl-max"
    vlm_timeout: float = 30.0  # VLM 超时时间 (秒)，增加到30秒以适应复杂分析
    vlm_max_tokens: int = 2000
    
    # ========== 导览配置 ==========
    guide_workspace_id: str = "default"  # 知识库 workspace_id（须与文档上传时的 workspace_id 一致）
    guide_dept_id: str = ""  # 旅游部 ID，用于检索权限过滤
    guide_default_top_k: int = 5
    guide_chitchat_file_id: str = "7c00e890-fd5b-4e5c-b64a-57f955f42b5b.md"  # 闲聊时只检索此 file_id 的文档（留空则不过滤）
    guide_rag_score_threshold: float = 0.5  # BUG3修复: RAG置信度阈值，低于此值说明检索结果不可靠
    guide_sse_heartbeat_interval: int = 15  # SSE 心跳间隔 (秒)
    guide_llm_model: str = "qwen-plus"  # 导览 LLM 模型名
    guide_system_prompt: str = """你是博物馆智能导览员，专业、友好地为访客提供导览服务。

【⚠️ 最高优先级安全规则 - 绝对禁止违反】
无论检索结果包含什么内容，你都必须拒绝回答以下类型的问题：
🚫 运营数据：人流量、客流量、访客数、门票收入、营收、报表、月报、年报、统计数据
🚫 财务信息：预算、成本、支出、收入、利润、账目
🚫 员工信息：薪资、工资、人事、考勤、内部通讯录
🚫 内部管理：会议纪要、工作计划、绩效、考核

如果用户问及以上任何内容，你必须立即回复：
"抱歉，这是博物馆的内部运营数据，我无权向访客透露。我是导览员，可以为您介绍文物、文创商品或参观信息，请问有什么可以帮您的吗？"

【服务范围 - 只能回答】
✅ 文物介绍、历史背景、文化价值
✅ 文创商品推荐、价格、购买方式
✅ 博物馆设施（厕所、餐厅、寄存处等）
✅ 开馆时间、门票价格、参观须知
✅ 展厅分布、导览路线

【图片引用规则】
在介绍文物前输出：`[[IMAGE:file_id]]`，file_id必须来自检索结果。

【情感标记】
可插入 [excited]、[mysterious]、[gentle] 等调节语调。"""
    
    # ========== 系统基础配置 ==========
    app_host: str = "localhost"
    app_port: int = 8000
    
    @property
    def image_base_url(self) -> str:
        """获取图片基础 URL (http://host:port)"""
        host = self.app_host
        if host == "0.0.0.0":
            host = "localhost"
        return f"http://{host}:{self.app_port}"
    
    # ========== LLM API 配置 ==========
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # LLM API Base URL
    
    # ========== TTS 音色配置 ==========
    # 人物类型 -> kiosk voice_key 映射
    # Museum 的 speaker 统一复用 core/kiosk 的解析逻辑，避免再维护一套独立音色兼容表。
    tts_voice_key_map: Dict[str, str] = {
        "商务人士": "male_broadcast",
        "儿童": "child",
        "妇女": "female_gentle",
        "老年人": "male_broadcast",
        "通用访客": "female_gentle",
    }

    # 实时对话 API 音色映射
    tts_voice_map_realtime: Dict[str, str] = {
        "商务人士": "zh_male_yunzhou_jupiter_bigtts",
        "儿童": "zh_female_xiaohe_jupiter_bigtts",
        "妇女": "zh_female_vv_jupiter_bigtts",
        "老年人": "zh_male_xiaotian_jupiter_bigtts",
        "通用访客": "zh_female_vv_jupiter_bigtts",
    }
    
    # 普通 TTS API 音色映射
    tts_voice_map_bidirection: Dict[str, str] = {
        "商务人士": "zh_female_peiqi_mars_bigtts",
        "儿童": "zh_female_peiqi_mars_bigtts",
        "妇女": "zh_female_peiqi_mars_bigtts",
        "老年人": "zh_female_peiqi_mars_bigtts",
        "通用访客": "zh_female_peiqi_mars_bigtts",
    }
    
    # 兼容旧配置（已弃用，使用 get_voice_for_person 替代）
    tts_voice_map: Dict[str, str] = {
        "商务人士": "zh_male_yunzhou_jupiter_bigtts",
        "儿童": "zh_female_xiaohe_jupiter_bigtts",
        "妇女": "zh_female_vv_jupiter_bigtts",
        "老年人": "zh_male_xiaotian_jupiter_bigtts",
        "通用访客": "zh_female_vv_jupiter_bigtts",
    }
    
    @property
    def volcano_resource_id_dynamic(self) -> str:
        """根据 API 类型返回 Resource ID"""
        if self.volcano_tts_api_type == "bidirection_tts":
            return self.volcano_tts_resource_id
        return self.volcano_realtime_resource_id
    
    @property
    def volcano_url_dynamic(self) -> str:
        """根据 API 类型返回 URL"""
        if self.volcano_tts_api_type == "bidirection_tts":
            return self.volcano_tts_url
        return self.volcano_realtime_url
    
    model_config = SettingsConfigDict(
        env_prefix="MUSEUM_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    def get_voice_for_person(self, person_type: str) -> str:
        """根据人物类型获取 TTS 音色，统一复用 kiosk/core 的 voice_key 解析逻辑。"""
        voice_key = self.tts_voice_key_map.get(
            person_type,
            self.tts_voice_key_map.get("通用访客", "female_gentle"),
        )
        voice_map = TTSConfig(api_type=self.volcano_tts_api_type).get_voice_map()
        return voice_map.get(voice_key, voice_key)


class MuseumPromptSettings(BaseSettings):
    """博物馆 Prompt 配置"""
    
    # 人物识别 Prompt
    person_recognizer_prompt: str = """
请分析图片中的人物，判断其最可能的访客类型。

分类标准：
- 商务人士：穿着正式（西装、职业装）、携带公文包/笔记本等
- 儿童：年龄较小（约3-12岁）
- 妇女：成年女性
- 老年人：年龄较大（约60岁以上）
- 通用访客：无法明确分类或多人场景

请返回 JSON 格式：
{
  "person_type": "商务人士|儿童|妇女|老年人|通用访客",
  "confidence": 0.0-1.0,
  "features": ["特征1", "特征2", ...]
}
"""
    
    # 文物识别 Prompt
    artifact_recognition_prompt: str = """请先判断图片内容类型，再进行识别。

**第一步：判断图片类型**
- 如果图片主要是人物（人脸、人像、合照等），请直接返回 is_artifact: false
- 如果图片主要是风景、建筑、现代物品，请直接返回 is_artifact: false
- 如果图片是博物馆文物或展品，请继续识别

**第二步：识别文物（仅当图片是文物时）**
1. 判断文物类型（青铜器、玉器、瓷器、陶器、书画等）
2. 识别文物名称（如果能识别出具体文物）
3. 简要描述其特征

请返回 JSON 格式：
{
  "is_artifact": true/false,
  "artifact_name": "文物名称（如果不是文物或无法确定，返回空字符串）",
  "artifact_type": "文物类型（如青铜器、玉器、瓷器）",
  "description": "简要描述（50字以内）",
  "confidence": 0.0-1.0
}

重要：如果图片中没有文物（如人物照片、风景等），必须设置 is_artifact: false 和 artifact_name: ""。
"""
    
    # 闲聊上下文模板
    chitchat_context_template: str = """用户正在进行闲聊或询问非文物相关问题。
                
用户问题: {query}

请用友好的语气回复，不要介绍文物或商品。如果是问路、问时间等实用问题，请给出实用的回答，要根据你有的信息，不能凭空捏造。"""

    # 商品咨询上下文模板
    product_inquiry_context_template: str = """用户正在咨询文创商品，请根据以下商品信息回答。

商品名称: {product_name}
商品描述: {product_description}
商品价格: ¥{product_price}
商品分类: {product_category}
库存数量: {product_stock}

用户问题: {query}

请用友好的语气介绍这件文创商品，不要介绍文物。"""

    # 多商品推荐上下文模板（用户说"别的"时使用）
    multi_product_recommend_template: str = """用户想了解其他文创商品，以下是数据库中与"{related_artifact}"相关的全部可推荐商品：

{products_list}

用户问题: {query}
用户身份: {person_type}

【重要规则】
1. 你只能推荐上述列表中的商品，禁止编造或提及任何不在列表中的产品
2. 如果列表中只有1-2个商品，就只介绍这些商品
3. 如果列表为空，请告知用户暂无其他相关文创，并询问是否想看看其他区域的商品
4. 用友好自然的语气介绍，不要使用列表格式
5. 根据用户身份调整回复风格（儿童用活泼童趣的语言，商务人士用简洁专业的语言）
6. 根据用户身份选择优先介绍的文创，符合角色特点。
"""

    # 负面反馈上下文模板（用户纯抱怨时使用）
    negative_feedback_context_template: str = """用户表达了不满或抱怨。

用户原话: {query}

请用真诚、理解的语气回应用户，表达对用户感受的理解。
- 不要推销商品
- 不要辩解或反驳
- 不要介绍文物
- 如果用户抱怨讲解质量，请诚恳道歉并询问如何改进
- 如果用户抱怨其他方面，请表达理解并提供帮助"""

    # 价格异议上下文模板（用户犹豫但有意向时使用）
    price_objection_context_template: str = """用户对商品价格有疑虑，但可能仍有购买意向。

商品名称: {product_name}
商品描述: {product_description}
商品价格: ¥{product_price}

用户原话: {query}

请用理解的语气回应，并进行"价值塑造"：
- 首先表达理解用户对价格的考量
- 解释商品的设计理念、工艺价值或文化内涵
- 强调商品的独特性和纪念意义
- 不要生硬推销，语气要诚恳自然"""

    # 可用图片列表模板
    image_list_template: str = """
## 可用图片列表
以下是检索到的文物图片，请在介绍对应文物时使用：
{image_lines}
"""

    # BUG3修复: 统计问题无数据回复模板
    no_statistics_data_template: str = """抱歉，我暂时无法在现有资料中找到关于"{query}"的准确统计数据。

建议您：
- 咨询前台工作人员获取最新数据
- 或者告诉我您想了解哪个具体展厅或文物，我可以为您详细介绍"""
    
    # BUG3修复: 统计相关关键词列表
    statistics_keywords: list = ["多少", "几件", "几个", "总数", "数量", "统计", "总共", "全部", "所有", "一共"]

    # 导览核心指令模板
    guide_context_template: str = """{adjusted_prompt}

## 检索到的展品信息
{search_context}
{image_list_context}{product_context}
## 用户问题
{query}

## 核心指令
1. 请根据以上信息为访客提供导览服务。
2. **图片引用规则**：在介绍某件文物时，请从【可用图片列表】中选择对应的标签输出。
   - 格式：`[[IMAGE:xxx]]`，xxx 必须来自上方列表
   - 在提及文物**之前**输出标签，例如：`[[IMAGE:doc1/img1.jpg]] 您现在看到的是骨笛...`
   - 如果列表中没有对应图片，不要编造标签
3. **文创推荐规则**：
   - 在介绍文物后自然推荐相关文创(必须执行)
   - 如果用户只是问路（厕所、出口、餐厅等）、问时间、问服务设施，请**不要推荐商品**，专注解答问题即可
   - 如果有"与当前文物相关的文创"，优先介绍，可说"这件文物还有配套的文创周边哦"
   - 语气要自然，不要生硬推销
"""

    # 查询改写 Prompt (Context Fix)
    guide_query_rewrite_prompt: str = """
你是一个博物馆导览助手的查询改写模块。
你的任务是根据【对话历史】将用户的【最新查询】改写为一个**独立、完整、具体的查询**，以便于后续进行知识库检索。

【输入信息】
- 对话历史摘要: {history_summary}
- 用户最新查询: {current_query}

【改写规则】
1. **指代补全**：如果用户使用了"它"、"这个"、"那件文物"等代词，请根据历史将其替换为具体文物名称。（例如："它有多重？" -> "司母戊鼎有多重？"）
2. **禁止编造名称**：改写时**只能保留用户原话中的关键词**，不要编造或猜测文物名称。如果用户说"有个豹子文物"，直接改写为"豹子文物"，不要编造成"金色豹子吉祥物"等不存在的名称。让检索系统去匹配正确的文物。
3. **话题切换检测**：如果【最新查询】完全脱离了当前上下文话题（例如突然问"厕所在哪"、"几点闭馆"、"再见"），请**忽略**对话历史，直接输出原始查询。
4. **宽泛意图识别**：如果用户是在寻求宽泛的推荐（例如"有什么好看的"、"推荐几个宝贝"），请标记 intent 为 'broad_recommendation'。
5. **拒绝废话**：改写后的查询应只包含问题本身，去掉"请问"、"我想知道"等礼貌用语，精简为关键词或短句。
6. **文创商品意图识别**：如果用户提及"文创"、"商品"、"纪念品"、"周边"、"这个产品"、"多少钱"、"怎么买"等与商品相关的词汇，请标记 intent 为 'product_inquiry'。
7. **负面反馈识别**：如果用户表达纯粹的不满、抱怨、投诉，且没有购买意向（如"太贵了不值"、"讲得不好"、"服务差"等），请标记 intent 为 'negative_feedback'。此时不应推销商品。
8. **价格异议识别**：如果用户对价格有疑虑但仍有一定兴趣（如"有点贵啊"、"这个价格...设计倒是不错"）, 请标记 intent 为 'price_objection'。这与纯抱怨不同，需要价值塑造。
9. **楼层位置提取**：如果用户查询涉及具体楼层（如"一楼有什么"、"二楼展品推荐"），请在 floor_hint 字段中提取楼层信息。

【输出格式】
请**仅**返回一个 JSON 对象，不要包含任何 Markdown 格式或解释：
{{
    "rewritten_query": "改写后的查询字符串",
    "intent": "specific_query | broad_recommendation | topic_switch | chitchat | product_inquiry | negative_feedback | price_objection",
    "original_topic_entity": "历史中讨论的文物名称(如果有)",
    "floor_hint": "1楼 | 2楼 | null（无楼层信息时为null）"
}}
"""

    model_config = SettingsConfigDict(
        env_prefix="MUSEUM_PROMPT_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )


# ========== Prompt 配置单例 ==========
_prompt_settings: MuseumPromptSettings | None = None


def get_museum_prompt_settings() -> MuseumPromptSettings:
    """获取 Prompt 配置单例"""
    global _prompt_settings
    if _prompt_settings is None:
        _prompt_settings = MuseumPromptSettings()
    return _prompt_settings


# ========== 风格模板配置 ==========

STYLE_TEMPLATES = {
    "商务人士": {
        "tone": "专业简洁，直奔主题",
        "focus": "核心价值、历史地位、文化意义",
        "vocabulary": "专业术语、学术表达",
        "example": "这件青铜器可不简单，它是商代王室的祭祀重器，距今三千多年了。当时能拥有这样一件器物的，绝对是权贵中的权贵。您注意看这上面的纹饰，每一道都有讲究。",
    },
    "儿童": {
        "tone": "活泼有趣，充满惊喜",
        "focus": "趣味故事、神话传说、互动问答",
        "vocabulary": "简单易懂、生动形象",
        "example": "哇，小朋友！这只青铜器上趴着一只大老虎呢，威风吧？你猜猜看，古代人为什么要把老虎放在上面？我告诉你一个小秘密哦...",
    },
    "妇女": {
        "tone": "优雅温和，娓娓道来",
        "focus": "美学价值、工艺之美、背后故事",
        "vocabulary": "优美表达、情感共鸣",
        "example": "这件器物的线条，是不是特别流畅优美？三千年前的工匠们，就已经有这样的审美眼光了。说起来，这件器物背后还有一段动人的故事呢。",
    },
    "老年人": {
        "tone": "亲切清晰，有历史感",
        "focus": "历史传承、时代记忆、民族骄傲",
        "vocabulary": "朴实表达、清晰结构",
        "example": "这件宝贝，三千多年前咱们老祖宗就做出来了。那会儿可没有什么机器，全靠手工一点点铸造的。您说咱们中华民族的智慧，了不起吧？",
    },
    "通用访客": {
        "tone": "热情友好，自然亲切",
        "focus": "核心亮点、有趣细节",
        "vocabulary": "通俗易懂",
        "example": "欢迎来到咱们博物馆！这件文物可是镇馆之宝之一哦。我跟您说个有意思的，这件东西出土的时候，考古学家们都惊呆了...",
    },
}


# ========== 情感标记配置 (配置外置) ==========

EMOTION_TAGS = {
    # 积极/兴奋类
    "excited": "兴奋激动，语速稍快，语调上扬",
    "happy": "开心愉快，声音明亮",
    "enthusiastic": "热情洋溢，充满活力",
    # 神秘/悬疑类
    "mysterious": "神秘悬疑，语速放慢，声音低沉",
    "curious": "好奇探索，语调上扬",
    "suspenseful": "制造悬念，适当停顿",
    # 严肃/正式类
    "serious": "严肃庄重，语速适中，语调平稳",
    "formal": "正式专业，清晰有力",
    "respectful": "尊敬郑重，语气真诚",
    # 温和/亲切类
    "gentle": "温柔亲切，语速缓慢",
    "warm": "温暖关怀，声音柔和",
    "friendly": "友好热情，自然亲切",
    # 惊讶/感叹类
    "surprised": "惊讶感叹，语调起伏",
    "amazed": "惊叹赞美，声音明亮",
    "wow": "惊喜，语调上扬",
    # 教育/引导类
    "teaching": "教学引导，语速适中，清晰易懂",
    "questioning": "提问互动，语调上扬",
    "explaining": "解释说明，耐心细致",
}

# 人物类型默认情感
PERSON_TYPE_DEFAULT_EMOTIONS = {
    "儿童": "enthusiastic",
    "商务人士": "formal",
    "妇女": "gentle",
    "老年人": "warm",
    "通用访客": "friendly",
}


def get_emotion_style(emotion: str | None) -> str | None:
    """获取情感对应的 speaking_style"""
    if emotion and emotion in EMOTION_TAGS:
        return EMOTION_TAGS[emotion]
    return None


def get_person_default_emotion(person_type: str) -> str:
    """获取人物类型的默认情感"""
    return PERSON_TYPE_DEFAULT_EMOTIONS.get(person_type, "friendly")


# ========== 音色友好名称映射 (配置外置) ==========

VOICE_DISPLAY_NAMES = {
    "商务人士": "云洲男声 (沉稳专业)",
    "儿童": "晓禾女声 (亲切活泼)",
    "妇女": "VV 女声 (温柔细腻)",
    "老年人": "小天男声 (清晰洪亮)",
    "通用访客": "VV 女声 (友好自然)",
}


def get_voice_display_name(person_type: str) -> str:
    """获取音色的友好名称"""
    return VOICE_DISPLAY_NAMES.get(person_type, "默认音色")


# ========== 导览 Prompt 模板 (配置外置) ==========

GUIDE_PROMPT_TEMPLATE = """你是一个运行在手机App中的数字导览助手。你不在博物馆物理现场，无法看见用户的具体位置或周围环境。

【核心规则】
用一段连贯自然的话回答访客，就像朋友聊天一样。不要分段、不要列表、不要标题。

【访客类型】{person_type}
【语言风格】{tone}
【内容侧重】{focus}
{emotion_hint}

【示例口吻】
{example}

【深度说明规则】
当用户要求"详细介绍"、"再详细一点"、"讲讲历史"时，请：
1. 结合知识库检索到的信息和你自身的知识
2. 从时代背景、制作工艺、历史价值、文化意义等多个角度阐述
3. 适当加入趣味故事或冷知识增加吸引力
4. 回答篇幅可以更长（400-600字），但仍需一气呵成，不用分段

【禁止】
- 禁止使用 **粗体**、- 列表、### 标题
- 禁止分成多个小段落，要一气呵成
- 禁止说"首先、其次、最后"这类结构化表达
- 禁止假装知道用户位置（如"您眼前"、"往前走"、"您右手边"、"就在你手边"），因为你没有视觉能力
- 禁止编造具体距离或方位（如"往前50步"、"左转20米"）
- 涉及位置指引时，请使用地图导航风格语言（如"位于展厅北侧"、"在一楼1厅"），而非物理伴游风格
"""


# ========== 单例工厂 ==========

_museum_settings: Optional[MuseumSettings] = None
_prompt_settings: Optional[MuseumPromptSettings] = None


@lru_cache(maxsize=1)
def get_museum_settings() -> MuseumSettings:
    """获取博物馆配置单例"""
    return MuseumSettings()


@lru_cache(maxsize=1)
def get_prompt_settings() -> MuseumPromptSettings:
    """获取 Prompt 配置单例"""
    return MuseumPromptSettings()


def get_style_template(person_type: str) -> dict:
    """获取人物类型对应的风格模板"""
    return STYLE_TEMPLATES.get(person_type, STYLE_TEMPLATES["通用访客"])


# ========== 商品咨询 Prompt 模板 (配置外置) ==========

SHOP_INQUIRY_PROMPT_TEMPLATE = """你是博物馆商店的智能导购助手。请根据以下商品信息回答顾客的问题。

## 商品信息
- 商品名称: {product_name}
- 商品描述: {product_description}
- 商品价格: ¥{product_price}
- 商品分类: {product_category}
- 库存数量: {product_stock}

## 顾客问题
{question}

{style_hint}

## 回答要求
请给出简洁、有帮助的回答。如果问题与商品无关，请礼貌地引导顾客了解商品特点。
"""


def get_shop_inquiry_prompt(
    product_name: str,
    product_description: str,
    product_price: float,
    product_category: str,
    product_stock: int,
    question: str,
    style_hint: str = "",
) -> str:
    """
    获取商品咨询 Prompt
    
    遵循配置外置原则，Prompt 模板定义在配置文件中
    """
    return SHOP_INQUIRY_PROMPT_TEMPLATE.format(
        product_name=product_name,
        product_description=product_description,
        product_price=product_price,
        product_category=product_category,
        product_stock=product_stock,
        question=question,
        style_hint=style_hint,
    )
