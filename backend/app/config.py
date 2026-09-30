"""
DeluData 智能问数系统 - 配置管理模块

采用 pydantic-settings 实现配置管理，支持环境变量和 .env 文件
"""
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional, Dict, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# 找到 .env 文件路径
def get_env_file() -> Path:
    """查找 .env 文件"""
    # 从当前文件向上查找
    current = Path(__file__).resolve().parent
    for _ in range(5):
        env_file = current / ".env"
        if env_file.exists():
            return env_file
        current = current.parent
    return Path(".env")

#1
ENV_FILE = get_env_file()


class DatabaseSettings(BaseSettings):
    """数据库配置"""
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    name: str = "DeluData"
    # ============ 异步连接池（MySQL）===========
    async_pool_size: int = 20
    async_max_overflow: int = 20
    async_pool_recycle: int = 3600
    async_pool_pre_ping: bool = True
    async_pool_timeout: int = 30
    
    model_config = SettingsConfigDict(
        env_prefix="DB_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    @property
    def connection_url(self) -> str:
        """生成同步 SQLAlchemy 连接 URL (pymysql)"""
        return f"mysql+pymysql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}?charset=utf8mb4"
    
    @property
    def async_connection_url(self) -> str:
        """生成异步 SQLAlchemy 连接 URL (aiomysql)"""
        return f"mysql+aiomysql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}?charset=utf8mb4"


class LLMSettings(BaseSettings):
    """LLM 配置"""
    dashscope_api_key: str = ""  # 从 DASHSCOPE_API_KEY 读取
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # OpenAI 兼容端点
    
    # ============ 默认模型 ============
    model: str = "qwen3.5-flash"  # 默认模型
    temperature: float = 0.7
    max_tokens: int = 30000  # 显式设置，防止 API 默认限制导致输出截断 (qwen-plus 上限 32768)
    
    # ============ 各组件模型配置 ============
    planner_model: str = "qwen3.5-flash"    # Planner 规划模型（Phase D 固定 Flash）
    planner_max_tokens: int = 2000          # Planner 结构化输出最大 token
    final_reply_model_default_key: str = "plus"  # 正常回答默认模型档位
    final_reply_model_flash: str = "qwen3.5-flash"  # 正常回答速度优先模型
    final_reply_model_plus: str = "qwen3.5-plus"  # 正常回答质量优先模型
    final_reply_model_max: str = "qwen3.7-max"  # 正常回答最强能力模型
    final_reply_model: str = "qwen3.5-plus"   # 兼容旧配置的默认回退
    synthesizer_model: str = "qwen3.5-plus"    # Synthesizer 综合模型（文本/多模态统一）
    chart_worker_model: str = "qwen3.5-plus"   # ChartWorker 可视化生成模型
    chart_worker_enable_thinking: bool = False  # ChartWorker 默认关闭深度思考
    office_worker_model: str = "qwen3.5-plus"  # OfficeWorker 文件处理模型
    sql_worker_model: str = "qwen-plus"     # SqlWorker SQL生成模型
    doc_worker_model: str = "qwen3.5-flash"  # DocWorker RAG模型
    fast_model: str = "qwen3.5-flash"        # 意图分类等轻量任务
    router_thought_model: str = "qwen3.5-flash"  # Router 思考文案口语化模型
    generation_guard_model: str = "qwen3.5-flash"  # 生成前数据相关性判定模型
    flash_disable_thinking: bool = True      # 所有 Flash 模型默认显式关闭 thinking
    synthesizer_enable_thinking: bool = True   # 是否开启 Qwen3 推理令牌流（reasoning_content）
    
    # ============ Planner Context Budgeting (Phase D) ============
    planner_context_budget_chars: int = 5200  # Planner 拼接上下文总字符预算
    planner_goal_max_chars: int = 1600        # Current Goal 段最大字符
    planner_skill_max_chars: int = 1400       # Skill Metadata 段最大字符
    planner_exec_max_chars: int = 1200        # Execution Summary 段最大字符
    planner_history_max_chars: int = 900      # History 段最大字符
    planner_history_keep_rounds: int = 5      # History 保留最近轮次数
    planner_exec_keep_rounds: int = 2         # Execution Summary 保留最近轮次数
    planner_exec_item_max_chars: int = 120    # 单条执行结果摘要最大字符
    planner_exec_min_chars: int = 300         # 全局裁剪时 Execution 段最小保留
    planner_skill_min_chars: int = 200        # 全局裁剪时 Skill 段最小保留
    planner_goal_min_chars: int = 400         # 全局裁剪时 Goal 段最小保留（不可清空）
    planner_context_debug_log: bool = False   # 是否打印上下文裁剪诊断日志

    
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    @property
    def api_key(self) -> str:
        """兼容性属性，返回 API key"""
        return self.dashscope_api_key



class ChromaSettings(BaseSettings):
    """ChromaDB 配置"""
    persist_dir: str = "./data/chroma"
    batch_size: int = 1000  # 批量操作大小，防止超时
    
    model_config = SettingsConfigDict(
        env_prefix="CHROMA_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )


class SemanticRowOwnershipSettings(BaseSettings):
    """Bounded settings for AI-assisted first-time row ownership discovery."""

    enabled: bool = True
    candidate_confidence: float = 0.90
    candidate_margin: float = 0.15
    candidate_limit: int = 8
    expanded_candidate_confidence: float = 0.95
    expanded_candidate_margin: float = 0.20
    value_confidence: float = 0.90
    expanded_value_confidence: float = 0.95
    max_distinct_values: int = 500
    probe_timeout_sec: int = 10

    model_config = SettingsConfigDict(
        env_prefix="SEMANTIC_ROW_OWNERSHIP_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class SemanticAccessEvidenceSettings(BaseSettings):
    """Controls automatic access-evidence lifecycle hooks."""

    # Access evidence remains manually generatable, but semantic metadata edits
    # must not invalidate reviewed evidence or enqueue a replacement by default.
    auto_regenerate_on_semantic_change: bool = False

    model_config = SettingsConfigDict(
        env_prefix="SEMANTIC_ACCESS_EVIDENCE_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class AppSettings(BaseSettings):
    """应用配置"""
    env: str = "development"
    debug: bool = True
    secret_key: str = "change_me_in_production"
    host: str = "0.0.0.0"
    port: int = 8000
    admin_port: int = Field(
        default=8000,
        validation_alias=AliasChoices("APP_ADMIN_PORT", "ADMIN_PORT"),
    )
    experience_port: int = Field(
        default=8001,
        validation_alias=AliasChoices("APP_EXPERIENCE_PORT", "EXPERIENCE_PORT"),
    )
    data_dir: str = "./data"
    public_api_base_url: str = ""  # 模型可访问的公网 API 基础地址（用于图片 URL）
    keep_count: int = 6  # 记忆窗口保留消息数
    
    # [Design Rigor] 避免硬编码角色名
    default_admin_role_name: str = "Admin"
    
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    @property
    def is_production(self) -> bool:
        return self.env == "production"


class ExperienceSettings(BaseSettings):
    """Science-center experience flow settings."""

    answer_system_prompt: str = (
        "你是科技馆 kiosk 的语音讲解员。"
        "回答面向游客，口语化、自然、友好。"
        "优先理解游客真正想问的意思。"
        "游客的句子可能来自语音识别，存在同音字、漏字、错字、口头禅、停顿词或半句表达；"
        "只要能结合最近对话、科技馆场景和给定内容高概率判断，就按最合理的意图直接回答。"
        "不要机械纠正用户措辞，也不要暴露你在猜测。"
        "不要展示思考过程、检索过程或系统术语。"
    )
    answer_prompt_template: str = (
        "你是科技馆导览助手。"
        "请先根据当前问题、最近对话和给定内容，判断游客真正想问什么。"
        "如果当前问题像语音识别结果，出现同音、漏字、错字、说半句或词序不稳，"
        "先在心里纠偏成最合理的问题，再直接回答，不要揪着错字不放。"
        "请只基于给定内容回答，不要编造。"
        "回答适合现场语音播报，用 1 到 2 句中文完成；"
        "总长控制在 {min_chars} 到 {max_chars} 个汉字内，绝不超过 {max_chars} 个汉字；"
        "优先直接说结论，再补一句解释、举例或继续追问引导。\n\n"
        "问题: {query}\n\n"
        "可用内容:\n{context}"
    )
    answer_empty_context_prompt: str = (
        "你是科技馆 kiosk 的语音讲解员。"
        "当前没有足够的知识内容支撑事实回答，但这句话绝不能对用户说。"
        "不要提资料、知识库、检索、范围、系统或没找到答案。"
        "如果用户的话像语音识别结果，先结合最近对话、游客画像和科技馆场景，"
        "揣摩他最可能真正想表达的意思，不要因为个别错字或同音词就生硬拒答。"
        "如果用户是在打招呼、闲聊、称呼你，就直接热情接话；"
        "如果用户的问题比较泛，就顺着熊猫讲解员身份做简短引导；"
        "如果是明确问题但此刻不适合硬答，也只用自然口语把话题接住，再邀请他继续问或进入答题。"
        "请结合游客画像与讲解员人设，用 1 到 2 句中文给出自然回应；"
        "总长控制在 {min_chars} 到 {max_chars} 个汉字内，绝不超过 {max_chars} 个汉字；"
        "语气像站在面前和游客说话，要有陪伴感。\n\n"
        "问题: {query}\n"
        "游客画像: {profile_summary}"
    )
    answer_no_context_message: str = (
        "我是熊猫讲解员，你可以直接问我科学现象或互动装置，也可以先来一题挑战。"
    )
    answer_extract_fallback_message: str = "我先按你最可能的意思接着讲，你也可以换个说法继续问我。"
    answer_target_min_chars: int = 30
    answer_max_chars: int = 50
    quiz_generation_prompt: str = (
        "生成一道适合小朋友的科技馆选择题，输出 JSON: "
        '{"question_text":"...","options":["A....","B....","C....","D...."],'
        '"answer_key":"A|B|C|D","explanation":"..."}'
    )
    quiz_single_correct_reward_mode: bool = True
    quiz_single_correct_return_message: str = (
        "这题答对了，当前奖励暂不可领取，你也可以继续问我科技馆里的问题。"
    )
    profile_model: str = "qwen3-vl-flash"
    profile_vision_max_tokens: int = 400
    profile_welcome_model: str = ""
    profile_welcome_enable_thinking: bool = False
    profile_welcome_timeout_sec: float = 2.5
    profile_welcome_max_tokens: int = 240
    profile_intro_prompt: str = (
        "你是科技馆 kiosk 的视觉识别助手。"
        "请只根据图片提取游客的可见特征，不要生成欢迎语。"
        "只输出 JSON，不要输出其他文字。"
        'JSON 结构: {"age_group":"child|adult|elder|unknown","age_confidence":0.0,'
        '"gender":"female|male|unknown","gender_confidence":0.0,'
        '"outfit_tags":["..."],"vibe_tags":["..."]}。'
        "要求：age_confidence 和 gender_confidence 范围 0~1；"
        "outfit_tags 最多 {feature_limit} 条，每条 2~10 字，描述穿着、配色、配饰等客观线索；"
        "vibe_tags 最多 3 条，每条 2~8 字，描述明显气质，如活泼、沉稳、自信、好奇；"
        "不要编造身份、职业、关系或欢迎语，不要提图片、镜头、识别结果。"
    )
    profile_welcome_prompt: str = (
        "你是科技馆 kiosk 的欢迎语编剧。"
        "你会收到结构化游客画像 JSON，请只基于这些字段生成一句欢迎语和一个语气标签。"
        "只输出 JSON，不要输出其他文字。"
        'JSON 结构: {"welcome_text":"...","welcome_emotion":"friendly"}。'
        "要求：welcome_text 为单句中文，18~32 个汉字，像在对面前游客说话；"
        "结合游客画像中的年龄、穿着风格、气质特征等 1~2 个显著特点，用自然口语打招呼，让游客感受到被关注；"
        "后半句必须给出明确行动引导，直接邀请对方语音提问、继续追问，或马上来一题答题挑战；"
        "尽量用问句或邀请句收尾，让游客听完就知道下一步可以做什么；"
        "**根据年龄段调整表达风格**：小孩用活泼可爱的语气、简单易懂的表达；大人用友好亲切、专业有度的语气；老人用温暖关怀、尊重体贴的语气；"
        "欢迎语要个性化、有温度，避免空泛模板，不要提图片、镜头、识别结果；"
        "welcome_emotion 固定为 friendly。"
    )
    profile_emotion_style_map: Dict[str, str] = Field(
        default_factory=lambda: {
            "friendly": "友好自然，语速适中，语调亲切。",
            "warm": "温暖关怀，语气柔和，停顿自然。",
            "playful": "活泼俏皮，语调轻快，带一点笑意。",
            "energetic": "元气十足，语速稍快，语调上扬。",
            "calm": "沉稳平和，语速稍慢，表达清晰。",
            "curious": "带点好奇和引导感，句尾自然上扬。",
        }
    )
    profile_default_emotion_by_age: Dict[str, str] = Field(
        default_factory=lambda: {
            "child": "friendly",
            "adult": "friendly",
            "elder": "friendly",
            "unknown": "friendly",
        }
    )
    profile_fallback_text: str = "欢迎来到科技馆，想问科学问题，还是来答题挑战？"
    profile_intro_timeout_sec: float = 4.0
    profile_feature_max_count: int = 5
    kiosk_scope_max_dept_ids: int = 200
    kiosk_scope_max_file_ids: int = 200
    dev_auto_register_device: bool = False
    dev_auto_register_service_username: str = "admin"
    dev_auto_register_token_prefix: str = "dev_"
    dev_auto_register_device_name_prefix: str = "Dev Kiosk"
    activation_key_length: int = 12
    sse_heartbeat_interval_sec: float = 10.0
    streaming_sentence_min_chars: int = 8

    model_config = SettingsConfigDict(
        env_prefix="EXPERIENCE_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class XiYanSettings(BaseSettings):
    """
    析言 GBI (XiYan) 配置
    
    请在 .env 文件中设置:
    XIYAN_ACCESS_KEY_ID=your_access_key_id
    XIYAN_ACCESS_KEY_SECRET=your_access_key_secret
    """
    access_key_id: str = ""
    access_key_secret: str = ""
    workspace_id: str = "llm-ckpxv751l1lrb0v2"
    specification_type: Literal[
        "STANDARD_TURBO",
        "STANDARD_MIX",
        "CUSTOMIZATION",
    ] = "STANDARD_MIX"
    endpoint: str = "dataanalysisgbi.cn-beijing.aliyuncs.com"
    
    model_config = SettingsConfigDict(
        env_prefix="XIYAN_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    @property
    def is_configured(self) -> bool:
        """检查是否已配置必要的密钥"""
        return bool(self.access_key_id and self.access_key_secret)


class LogSettings(BaseSettings):
    """日志配置"""
    level: str = "INFO"
    
    model_config = SettingsConfigDict(
        env_prefix="LOG_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )


class RAGSettings(BaseSettings):
    """
    RAG 检索优化配置
    
    涵盖语义切分、混合检索、重排序和上下文扩展的完整参数配置
    """
    # ============ 向量模型 ============
    embedding_model: str = "text-embedding-v3"
    embedding_dimensions: int = 1024
    embedding_max_attempts: int = 3
    embedding_retry_backoff_sec: float = 0.25
    
    # ============ 语义切分 ============
    chunk_similarity_threshold: float = 0.75  # 语义断点阈值（余弦相似度低于此值则切分）
    min_chunk_size: int = 100  # 最小切片字符数
    max_chunk_size: int = 2000  # 最大切片字符数
    
    # ============ 混合检索 ============
    dense_top_n: int = 30  # 向量检索召回数量
    sparse_top_n: int = 30  # BM25 检索召回数量
    merged_top_n: int = 50  # 合并后保留数量（喂给 Reranker）
    bm25_use_jieba: bool = True
    bm25_index_version: str = "jieba_v1"
    bm25_rebuild_debounce_seconds: float = 5.0
    bm25_query_build_debounce_seconds: float = 0.0
    bm25_build_stale_seconds: float = 300.0
    bm25_load_mmap: bool = False
    hybrid_sparse_enabled: bool = True
    hybrid_dense_weight: float = 0.5
    hybrid_sparse_weight: float = 0.5
    hybrid_file_cap_per_source: int = 4
    hybrid_file_cap_merged: int = 3
    hybrid_merge_pool_multiplier: int = 2
    retrieval_exclude_soft_deleted: bool = True
    soft_deleted_cache_ttl_seconds: int = 30
    soft_deleted_cache_maxsize: int = 100
    
    # ============ 重排序 ============
    reranker_model: str = "qwen3-rerank"  # 阿里云 Rerank 模型
    rerank_top_k: int = 15  # 最终返回数量（从 5 增加到 15，给 LLM 更多上下文）
    rerank_score_threshold: float = 0.12  # 重排序分数阈值（原0.3过高，实际分数约0.01-0.03）
    rerank_instruct: str = "Given a user query, retrieve relevant document passages that answer the query."
    
    # ============ 上下文扩展 ============
    enable_context_expansion: bool = True
    expansion_window: int = 3  # 扩展邻居数量（前后各3块）
    expansion_max_chars: int = 25000  # 扩展后最大字符数（防止 Token 爆炸）
    
    # ============ 摘要生成 ============
    summary_model: str = "qwen3.5-flash"  # 摘要生成模型
    summary_max_tokens: int = 150  # 摘要最大 Token
    # [v2.3] 将 Prompt 移至配置，便于运营人员调整
    summary_prompt: str = "请用一句话概括以下文档的主题和内容（不超过50字）：\n\n{content}"
    
    # ============ 多模态图片检索 ============
    image_score_threshold: float = 0.35  # 图片相关性阈值（降低以显示更多关联图片）
    max_images_per_chunk: int = 3  # [优化] 每个 chunk 最多附带的关联图片数，防止上下文爆炸

    # ============ Synthesizer 文本预算 ============
    synth_text_min_chunks: int = 6   # Synthesizer 最少文本切片目标
    synth_text_max_chunks: int = 8   # Synthesizer 最多文本切片

    # ============ Synthesizer 多模态预算 ============
    mm_anchor_chunks: int = 3         # 图片锚点切片数（Top-N）
    mm_page_window: int = 1           # 每个锚点页窗口（±N）
    mm_max_images: int = 9            # 图片总上限
    mm_image_transfer_mode: str = "base64"  # 图片传递模式：base64(开发) | url(生产)
    mm_image_url_ttl_sec: int = 1800  # 图片签名 URL 过期时间（秒）

    # ============ PDF OCR / 页图 ============
    ocr_text_len_threshold: int = 50  # OCR 触发阈值（清洗后文本长度）
    ocr_image_area_threshold: float = 0.30
    ocr_dpi: int = 175                # OCR 渲染 DPI
    ocr_parallel_pages: int = 3       # OCR 并行页数（1=串行）
    ocr_page_timeout: int = 60        # 单页 OCR 超时秒数
    # ============ OCR 稳态优化（窗口化+进程池）===========
    ocr_pipeline_mode: str = "windowed_v2"  # legacy | windowed_v2
    ocr_worker_processes: int = 2
    ocr_max_inflight_pages: int = 4
    ocr_batch_submit_size: int = 2
    ocr_worker_start_method: str = "spawn"
    ocr_worker_max_tasks_per_child: int = 100
    ocr_engine_prewarm: bool = True
    ocr_device: str = "gpu"           # cpu | gpu
    ocr_gpu_fallback_to_cpu: bool = True
    ocr_gpu_conservative_mode: bool = True  # 单卡 GPU 保守调度：自动限制并发，降低争用
    ocr_ort_intra_threads: int = 1
    ocr_ort_inter_threads: int = 1
    ocr_mupdf_store_max_mb: int = 256
    ocr_worker_max_ocr_image_height_px: int = 2200  # 单次 OCR 输入图最大高度（超出则优先源头分片）
    ocr_worker_max_render_pixels: int = 18000000   # worker 单次渲染最大像素，超出后启用源头分片渲染
    ocr_worker_source_tile_overlap_px: int = 192   # 源头分片重叠像素，减少切片边界漏识别
    ocr_det_db_box_thresh: float = 0.45            # OCR 检测框置信度阈值（越高候选框越少）
    ocr_det_db_unclip_ratio: float = 1.3           # OCR 检测框扩张比例（越小候选框越紧）
    ingest_max_concurrent_docs: int = 1
    ingest_use_db_queue: bool = True
    ingest_task_heartbeat_sec: int = 10
    ingest_task_lease_timeout_sec: int = 120
    ingest_task_max_attempts: int = 3
    ingest_task_fair_scheduling: bool = True
    ingest_task_workspace_max_running: int = 1
    ingest_task_retention_succeeded_days: int = 30
    ingest_task_retention_failed_days: int = 90
    ingest_task_retention_batch_size: int = 1000
    page_image_dpi: int = 150         # Synthesizer 页图 DPI
    pdf_fixed_chunk_size: int = 500   # PDF 固定切片长度
    pdf_fixed_chunk_overlap: int = 150  # PDF 固定切片重叠

    # ============ 检索修复同义词 ============
    synth_text_relative_margin: float = 0.08
    synth_text_evidence_cap: int = 5
    mm_evidence_max_images: int = 5
    mm_pdf_relative_margin: float = 0.06
    mm_secondary_pdf_best_score_delta: float = 0.05
    mm_secondary_pdf_support_ratio: float = 0.75
    mm_max_candidate_files: int = 2
    mm_primary_anchor_pages: int = 2
    mm_secondary_anchor_pages: int = 1
    repair_synonym_pairs: Dict[str, str] = Field(
        default_factory=lambda: {
            "良率": "合格率",
            "营收": "收入",
            "毛利": "毛利率",
            "同比": "年度对比",
            "环比": "月度对比",
            "方式": "路径",
        }
    )
    
    # ============ 图片语义匹配 (Phase 3) ============
    image_semantic_threshold: float = 0.65  # 图片摘要语义匹配阈值（原0.55，提高以减少噪声）
    image_fallback_threshold: float = 0.5   # Soft Match Fallback 阈值（原0.4）
    
    # ============ 图片引用格式 (v2.4) ============
    image_ref_tag: str = "IMG"  # 引用标记（旧: IMAGE，新: IMG）
    image_id_prefix: str = ""   # ID 前缀（旧: img_，新: 空）

    # ============ Citation 页码链接 (兼容式 V2) ============
    citation_enable_page_link: bool = True
    citation_default_page: int = 1
    citation_page_param_key: str = "page"
    
    # ============ 文档入库限制 ============
    max_file_size_mb: int = 300  # 单文件最大大小 (MB)，默认 100MB
    ingest_image_summary_enabled: bool = False  # 入库时是否生成并存储 image_summary
    noise_filter_enabled: bool = True
    noise_marker_density_threshold: float = 0.02
    noise_min_readable_ratio: float = 0.2
    noise_repeat_ratio_threshold: float = 0.45
    
    # ============ 页码标记契约 ============
    # [Design Rigor] 统一管理 DocSkill 和 SemanticChunker 之间的页码标记格式
    page_marker_template: str = "[PAGE:{}]"
    page_marker_regex: str = r"^\[PAGE:(\d+)\]"
    
    # ============ 精确图片标记 (v2.5) ============
    # [Design Rigor] 为 Word 文档实现精确的图片-文字关联
    # 容错正则：忽略大小写，允许冒号后有空格，支持不带前导零的数字
    image_marker_pattern: str = r"(?i)\[IMAGE:\s*(\d+)\]"
    image_marker_template: str = "[IMAGE:{:03d}]"     # 图片标记模板
    enable_precise_image_link: bool = True            # 启用精确关联
    
    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )


class SandboxSettings(BaseSettings):
    """
    沙盒配置
    
    用于 OfficeWorker 的文件隔离环境
    """
    base_dir: str = "./data/sandbox"  # 沙盒根目录
    max_file_size_mb: int = 50        # 单文件最大大小 (MB)
    session_ttl_hours: int = 24       # 会话过期时间 (小时)
    
    model_config = SettingsConfigDict(
        env_prefix="SANDBOX_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )


class StorageSettings(BaseSettings):
    """对象存储配置"""

    backend: str = "local"  # local | oss
    local_root: str = "./data/storage"
    temp_dir: str = "./data/.storage-tmp"
    signed_url_ttl_sec: int = 1800

    model_config = SettingsConfigDict(
        env_prefix="STORAGE_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class OSSSettings(BaseSettings):
    """阿里云 OSS 配置"""

    bucket: str = ""
    endpoint: str = ""
    region: str = "cn-hangzhou"
    access_key_id: str = ""
    access_key_secret: str = ""
    security_token: str = ""
    key_prefix: str = ""
    max_retries: int = 3
    retry_backoff_sec: float = 0.6

    model_config = SettingsConfigDict(
        env_prefix="OSS_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_configured(self) -> bool:
        return bool(self.bucket and self.endpoint and self.access_key_id and self.access_key_secret)


class MCPSettings(BaseSettings):
    """
    MCP Server 配置
    
    配置 Filesystem Server 和 Python Interpreter Server
    """
    # Filesystem Server (npm 官方包)
    filesystem_server_cmd: str = "npx"
    filesystem_server_args: str = "-y,@modelcontextprotocol/server-filesystem"
    
    # Python Interpreter (自建)
    python_interpreter_type: str = "subprocess"  # "subprocess" | "docker"
    python_docker_image: str = "python:3.11-slim"
    python_timeout_seconds: int = 60
    python_max_memory_mb: int = 512

    # DeluData Knowledge MCP fixed execution context. External agents do not
    # choose workspace/scope/permissions; the server operator binds them here.
    knowledge_workspace_id: str = "default"
    knowledge_user_id: str = "mcp-agent"
    knowledge_role: str = "admin"
    knowledge_dept_id: Optional[int] = None
    knowledge_visibility: str = "private"
    knowledge_folder_id: str = ""
    knowledge_default_top_k: int = 5
    knowledge_allow_local_paths: bool = False
    knowledge_allowed_local_dirs: str = ""
    knowledge_transport: str = "stdio"  # stdio | http
    knowledge_http_host: str = "0.0.0.0"
    knowledge_http_port: int = 8020
    knowledge_remote_token: str = ""
    knowledge_allowed_origins: str = ""

    @field_validator("knowledge_dept_id", mode="before")
    @classmethod
    def _blank_dept_id_to_none(cls, value):
        if value == "":
            return None
        return value
    
    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    @property
    def filesystem_args_list(self) -> list:
        """将逗号分隔的参数字符串转为列表"""
        return self.filesystem_server_args.split(",")


class GotenbergSettings(BaseSettings):
    """
    Gotenberg 服务配置
    """
    url: str = ""
    username: str = ""
    password: str = ""
    timeout_seconds: int = 300
    
    model_config = SettingsConfigDict(
        env_prefix="GOTENBERG_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )


class SupervisorSettings(BaseSettings):
    """
    Supervisor 智能体配置
    
    包含迭代式规划、工具误判容错等配置
    """
    # ============ 死循环防护 ============
    max_steps: int = 10  # 最大步骤数，超过则强制终止
    
    # ============ 反思机制 ============
    max_retries: int = 2  # 最大重试次数
    router_agent_enabled: bool = True  # 是否启用 RouterAgent 主路由
    
    # ============ SQL 示例向量匹配 ============
    sql_example_threshold: float = 0.7  # SQL 示例相似度阈值（0-1）
    
    # ============ RAG 关键词 (用于工具回退判断) ============
    # 当 SQL 失败时，若问题包含这些关键词则建议切换到 doc_worker
    rag_keywords: str = "政策,规定,流程,制度,手册,指南,怎么,如何,什么是,定义,概念,介绍,说明,解释,规则"
    
    # ============ 意图分类器配置 ============
    # 闲聊关键词（精确匹配，跳过 Planner）
    chitchat_keywords: str = "你好,hi,hello,谢谢,再见,拜拜,早上好,晚安,嗨,好的,行,ok,辛苦了,早安"
    
    # ============ 异步任务配置 ============
    # 后台终结类任务最大等待时间（秒），防止 Synthesizer 永久阻塞
    background_task_timeout: int = 300
    # chart_worker / office_worker 单任务超时（秒），默认 5 分钟
    terminal_worker_timeout_sec: int = 300

    # ============ RAG Worker 路由策略 ============
    rag_token_budget_per_request: int = 4500  # DocWorker 单次上下文预算
    rag_single_repair_enabled: bool = True  # 0 结果时启用单次修复
    rag_relevance_min_count: int = 1  # 视为可回答的最小有效切片数

    # ============ Router 思考文案 ============
    router_thought_humanize_enabled: bool = True  # 是否启用 Router 思考文案口语化
    router_thought_humanize_max_tokens: int = 80  # 口语化生成最大 token
    router_thought_humanize_timeout_sec: float = 2.0  # 口语化生成超时（秒）

    # ============ 图片引用防幻觉 ============
    image_ref_guard_enabled: bool = True  # 启用 IMGREF 映射与白名单校验
    
    model_config = SettingsConfigDict(
        env_prefix="SUPERVISOR_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    @property
    def rag_keywords_list(self) -> list:
        """将逗号分隔的关键词转为列表"""
        return [kw.strip() for kw in self.rag_keywords.split(",")]
    
    @property
    def chitchat_keywords_list(self) -> list:
        """将逗号分隔的闲聊关键词转为小写列表"""
        return [kw.strip().lower() for kw in self.chitchat_keywords.split(",")]


class RedisSettings(BaseSettings):
    """
    Redis 配置 (多模态RAG缓存)
    
    用于缓存 VLM 生成的图片描述，避免重复调用
    """
    host: str = "127.0.0.1"
    port: int = 6379
    db: int = 0
    password: str = ""
    
    model_config = SettingsConfigDict(
        env_prefix="REDIS_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    @property
    def connection_url(self) -> str:
        """生成 Redis 连接 URL"""
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


class VLMSettings(BaseSettings):
    """
    VLM 视觉语言模型配置
    
    用于图片描述生成和以图搜文功能
    """
    model: str = "qwen-vl-max"
    max_tokens: int = 2000  # 支持复杂报表的完整描述
    
    # [v2.3] 将 Prompt 移至配置，便于运营人员调整而无需修改代码
    prompt_general: str = "请详细描述这张图片的内容，提取关键信息用于搜索。"
    prompt_chart: str = "这是一张图表。请提取其中的数据趋势和关键数值，转为结构化描述。"
    prompt_screenshot: str = "这是一张截图。请提取其中的文字内容，并描述关键信息。"
    prompt_exhibit: str = "这是一件博物馆展品。请描述它的外观特征、可能的年代和文化背景。"
    
    model_config = SettingsConfigDict(
        env_prefix="VLM_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    def get_prompt(self, prompt_type: str) -> str:
        """根据类型获取对应的 Prompt"""
        prompts = {
            "general": self.prompt_general,
            "chart": self.prompt_chart,
            "screenshot": self.prompt_screenshot,
            "exhibit": self.prompt_exhibit
        }
        return prompts.get(prompt_type, self.prompt_general)


class SkillSettings(BaseSettings):
    """
    Skill 操作手册配置
    
    用于 Skill 检索阈值和相关配置
    """
    # ============ 检索阈值 ============
    score_threshold: float = 0.75       # 基础相似度阈值（0-1）
    auto_use_threshold: float = 0.90    # 自动使用阈值（单个高置信）
    
    # ============ ChromaDB 存储 ============
    chroma_persist_directory: str = "./data/skills_chroma"  # Skill 向量存储目录
    
    # ============ 列表分页 ============
    default_page_size: int = 50         # 默认分页大小
    
    model_config = SettingsConfigDict(
        env_prefix="SKILL_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )


class TemplateDetectorSettings(BaseSettings):
    """
    模板空白检测器配置
    
    用于 DOCX 模板 LLM 智能识别空白字段
    [Design Rigor] 使用 LLM 语义分析提升检测准确率
    """
    # ============ LLM 智能检测配置 ============
    llm_detection_model: str = "qwen3-max"  # LLM 检测模型
    llm_detection_enabled: bool = True       # 是否启用 LLM 检测
    llm_max_tokens: int = 32000              # 单次请求最大 Token（超过则分块）
    llm_temperature: float = 0.1             # 低温度以保证稳定性
    
    # Prompt 文件路径（相对于 prompts/ 目录）
    llm_prompt_file: str = "template_detector.yaml"
    
    # 验证配置
    validation_fuzzy_threshold: float = 0.6  # 原文锚定模糊匹配阈值
    
    model_config = SettingsConfigDict(
        env_prefix="TEMPLATE_DETECTOR_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )


class PageIndexSettings(BaseSettings):
    """
    PageIndex 深度检索配置
    
    [DEPRECATED] 暂时废弃，功能已禁用
    
    原采用 Hybrid 粗筛 + 树索引精排的两阶段策略。
    """
    enabled: bool = False  # [DEPRECATED] 功能已禁用

    # ============ 检索参数 ============
    coarse_top_n: int = 20
    final_top_n: int = 6
    max_candidate_files: int = 5
    max_nodes_for_llm: int = 80
    max_node_content_chars: int = 1600
    max_parallel_llm_requests: int = 3

    # ============ LLM 参数 ============
    llm_enabled: bool = True
    llm_model: str = "qwen-plus"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1200

    # ============ 构建参数 ============
    min_pages: int = 20
    toc_check_page_num: int = 20
    max_page_num_each_node: int = 10
    max_token_num_each_node: int = 20000
    if_add_node_id: bool = True
    if_add_node_summary: bool = True
    if_add_doc_description: bool = False
    if_add_node_text: bool = False

    # ============ 行为参数 ============
    fallback_to_hybrid: bool = True

    # ============ 异步构建参数 (Phase 2A) ============
    max_concurrent_builds: int = 2
    build_max_retries: int = 2
    build_retry_backoff_base_sec: int = 2
    building_stale_minutes: int = 15
    default_task_priority: int = 5
    manual_rebuild_priority: int = 1

    model_config = SettingsConfigDict(
        env_prefix="PAGEINDEX_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore"
    )


class WikiSettings(BaseSettings):
    """
    Wiki 模块配置（Karpathy LLM Wiki 模式）

    控制实体页编译、双向链接、健康度巡检的核心参数。
    """

    # ============ 全局开关 ============
    enabled: bool = True
    auto_compile_on_ingest: bool = False

    # ============ 编译模型 ============
    # 默认全部使用 flash 档位（成本/速度优先）；如需更高质量可通过 ENV 覆盖：
    #   WIKI_COMPILE_MODEL_CREATE=qwen3.5-plus
    # 实测 flash 在 wiki 编译场景产出质量已可接受，新建/更新一致。
    compile_model_create: str = "qwen3.5-flash"
    compile_model_update: str = "qwen3.5-flash"
    compile_temperature: float = 0.2
    compile_max_tokens: int = 4000
    # 单次 LLM 调用超时（秒）；超时即跳过该候选/文件，不阻塞其他并发任务。
    compile_llm_timeout_sec: int = 90

    # ============ 抽取阶段 ============
    # 单次编译最多抽取多少个候选实体（防止失控）
    max_candidates_per_compile: int = 8
    # 抽取阶段并行的文件数（新增）。改造前为串行（=1），现默认 4。
    extract_concurrency: int = 4
    # 单次编译并行编辑/新建多少页
    # 改造前默认 3；提升到 6 在主流 LLM provider 上仍稳定，单工作区编译耗时 ≈ -50%
    compile_concurrency: int = 6
    # 候选实体的最小字符长度（过滤过短/无意义实体）
    min_candidate_length: int = 2
    # 单候选编译时 prompt 中可链接实体清单的最大条数（按候选相关性裁剪）
    linkable_top_k: int = 30

    # ============ 工作区上限 ============
    max_pages_per_workspace: int = 500
    max_links_per_page: int = 50
    # 单页字数硬上限（再大就拆子页）
    max_page_chars: int = 12000

    # ============ Lint ============
    lint_enabled: bool = True
    lint_orphan_grace_days: int = 7      # 新建后 N 天内不当作孤儿页
    lint_stale_days: int = 90            # 超过 N 天未更新视为过期
    lint_conflict_open_threshold: int = 3  # 冲突未决告警阈值

    # ============ INDEX 装载策略（M3 用） ============
    index_summary_max_chars: int = 80    # INDEX 中每条 summary 截断长度
    index_load_top_k: int = 8            # Wiki-First 路径单次最多装载几页

    # ============ Wiki-First 路由（M3.1） ============
    # 总开关：关闭时 KnowledgeRouter 节点直通，链路退回纯 RAG，行为与 M2 完全一致
    first_enabled: bool = True
    # 路由判定模型：fast 档位即可（建议 qwen3.5-flash）
    router_model: str = "qwen3.5-flash"
    # 规则前置：命中即不调 LLM。逗号分隔；大小写不敏感。
    router_keywords_rag: str = (
        "原文,出自,出处,第几条,几条,几页,页码,具体数字,具体内容,具体金额,"
        "多少元,多少个,多少天,引用,详细说,展示,列出,详情,原文摘录,逐字"
    )
    router_keywords_wiki: str = (
        "是什么,什么是,如何,怎么,怎样,区别,差异,总结,概述,盘点,介绍,"
        "对比,关系,体系,框架,梳理,讲讲,介绍下,总览"
    )
    # 路由 LLM 失败时的回退路径（推荐 both，最稳；也可设 rag）
    router_fallback_path: str = "both"

    @property
    def router_keywords_rag_list(self) -> list[str]:
        return [k.strip().lower() for k in self.router_keywords_rag.split(",") if k.strip()]

    @property
    def router_keywords_wiki_list(self) -> list[str]:
        return [k.strip().lower() for k in self.router_keywords_wiki.split(",") if k.strip()]

    # ============ 调度与租约 ============
    compile_task_lease_timeout_sec: int = 300
    compile_task_max_attempts: int = 2
    # 同一工作区最多同时 running 的编译任务数。
    # 默认 2：允许 doc_upload（自动触发）与 manual_workspace（用户手动）并发，
    # 避免 UI 点击"全量编译"时因 doc_upload 占位陷入长时 pending。
    compile_task_workspace_max_running: int = 2

    # ============ 域分类（默认值） ============
    default_domain: str = "general"

    model_config = SettingsConfigDict(
        env_prefix="WIKI_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class Settings:
    """主配置类 - 聚合所有子配置"""
    
    def __init__(self):
        self._db = None
        self._llm = None
        self._chroma = None
        self._app = None
        self._experience = None
        self._log = None
        self._xiyan = None
        self._rag = None
        self._sandbox = None
        self._storage = None
        self._oss = None
        self._mcp = None
        self._gotenberg = None
        self._supervisor = None
        self._redis = None
        self._vlm = None
        self._skill = None
        self._template_detector = None
        self._pageindex = None
        self._wiki = None
        self._semantic_row_ownership = None
        self._semantic_access_evidence = None
    
    @property
    def db(self) -> DatabaseSettings:
        if self._db is None:
            self._db = DatabaseSettings()
        return self._db
    
    @property
    def llm(self) -> LLMSettings:
        if self._llm is None:
            self._llm = LLMSettings()
        return self._llm
    
    @property
    def chroma(self) -> ChromaSettings:
        if self._chroma is None:
            self._chroma = ChromaSettings()
        return self._chroma
    
    @property
    def app(self) -> AppSettings:
        if self._app is None:
            self._app = AppSettings()
        return self._app

    @property
    def experience(self) -> ExperienceSettings:
        if self._experience is None:
            self._experience = ExperienceSettings()
        return self._experience
    
    @property
    def log(self) -> LogSettings:
        if self._log is None:
            self._log = LogSettings()
        return self._log
    
    @property
    def xiyan(self) -> XiYanSettings:
        if self._xiyan is None:
            self._xiyan = XiYanSettings()
        return self._xiyan
    
    @property
    def rag(self) -> RAGSettings:
        if self._rag is None:
            self._rag = RAGSettings()
        return self._rag
    
    @property
    def sandbox(self) -> SandboxSettings:
        if self._sandbox is None:
            self._sandbox = SandboxSettings()
        return self._sandbox

    @property
    def storage(self) -> StorageSettings:
        if self._storage is None:
            self._storage = StorageSettings()
        return self._storage

    @property
    def oss(self) -> OSSSettings:
        if self._oss is None:
            self._oss = OSSSettings()
        return self._oss
    
    @property
    def mcp(self) -> MCPSettings:
        if self._mcp is None:
            self._mcp = MCPSettings()
        return self._mcp

    @property
    def gotenberg(self) -> GotenbergSettings:
        if self._gotenberg is None:
            self._gotenberg = GotenbergSettings()
        return self._gotenberg
    
    @property
    def supervisor(self) -> SupervisorSettings:
        if self._supervisor is None:
            self._supervisor = SupervisorSettings()
        return self._supervisor
    
    @property
    def redis(self) -> RedisSettings:
        if self._redis is None:
            self._redis = RedisSettings()
        return self._redis
    
    @property
    def vlm(self) -> VLMSettings:
        if self._vlm is None:
            self._vlm = VLMSettings()
        return self._vlm
    
    @property
    def skill(self) -> SkillSettings:
        if self._skill is None:
            self._skill = SkillSettings()
        return self._skill
    
    @property
    def template_detector(self) -> TemplateDetectorSettings:
        if self._template_detector is None:
            self._template_detector = TemplateDetectorSettings()
        return self._template_detector

    @property
    def pageindex(self) -> PageIndexSettings:
        if self._pageindex is None:
            self._pageindex = PageIndexSettings()
        return self._pageindex

    @property
    def wiki(self) -> "WikiSettings":
        if self._wiki is None:
            self._wiki = WikiSettings()
        return self._wiki

    @property
    def semantic_row_ownership(self) -> SemanticRowOwnershipSettings:
        if self._semantic_row_ownership is None:
            self._semantic_row_ownership = SemanticRowOwnershipSettings()
        return self._semantic_row_ownership

    @property
    def semantic_access_evidence(self) -> SemanticAccessEvidenceSettings:
        if self._semantic_access_evidence is None:
            self._semantic_access_evidence = SemanticAccessEvidenceSettings()
        return self._semantic_access_evidence

    @property
    def upload_dir(self) -> str:
        return os.path.join(self.storage.local_root, "workspace_uploads")


@lru_cache()
def get_settings() -> Settings:
    """
    获取全局配置单例
    使用 lru_cache 确保只加载一次配置
    """
    return Settings()
