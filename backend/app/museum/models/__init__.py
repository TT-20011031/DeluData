"""
博物馆模块 - Pydantic 数据模型

遵循设计原则：严格模式校验 (Schema Validation)
所有数据交互必须经由 Pydantic 层严格校验
"""
from datetime import datetime
from typing import Optional, List
from enum import Enum

from pydantic import BaseModel, Field


# ========== 枚举类型 ==========

class PersonType(str, Enum):
    """访客人物类型"""
    BUSINESS = "商务人士"
    CHILD = "儿童"
    WOMAN = "妇女"
    ELDERLY = "老年人"
    GENERAL = "通用访客"


class ProductCategory(str, Enum):
    """商品分类"""
    CREATIVE = "文创"
    SOUVENIR = "纪念品"
    REPLICA = "仿制品"
    BOOK = "书籍"
    OTHER = "其他"


class ProductStatus(str, Enum):
    """商品状态"""
    ACTIVE = "active"
    INACTIVE = "inactive"


# ========== 人物识别模型 ==========

class PersonAnalysisResult(BaseModel):
    """人物识别结果"""
    person_type: PersonType = Field(default=PersonType.GENERAL, description="识别出的人物类型")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="置信度")
    features: List[str] = Field(default_factory=list, description="识别到的特征列表")
    fallback: bool = Field(default=False, description="是否降级处理")
    reason: Optional[str] = Field(default=None, description="降级原因")


# ========== 导览请求/响应模型 ==========

class GuideStartRequest(BaseModel):
    """开始导览请求"""
    session_id: Optional[str] = Field(default=None, description="会话ID，不传则自动生成")
    query: Optional[str] = Field(default=None, description="用户问题")
    enable_tts: bool = Field(default=True, description="是否启用语音")
    visitor_uuid: Optional[str] = Field(default=None, description="访客 UUID")


class GuideChatRequest(BaseModel):
    """导览对话请求"""
    session_id: str = Field(..., description="会话ID")
    message: str = Field(..., min_length=1, description="用户消息")
    person_type: Optional[PersonType] = Field(default=None, description="人物类型，不传则使用会话缓存")
    enable_tts: bool = Field(default=True, description="是否启用语音")


class AdjustmentStep(BaseModel):
    """调节步骤"""
    step: str = Field(..., description="步骤标识: style|focus|vocabulary|voice")
    label: str = Field(..., description="步骤标签")
    value: str = Field(..., description="调节值")
    progress: int = Field(..., ge=0, le=100, description="进度百分比")


class AdjustmentResult(BaseModel):
    """调节结果"""
    adjusted_prompt: str = Field(..., description="调节后的提示词")
    voice_id: str = Field(..., description="TTS 音色ID")
    style_config: dict = Field(default_factory=dict, description="风格配置")


# ========== 商品模型 ==========

class ProductBase(BaseModel):
    """商品基础模型"""
    name: str = Field(..., min_length=1, max_length=255, description="商品名称")
    description: Optional[str] = Field(default=None, description="商品描述")
    price: float = Field(..., gt=0, description="商品价格")
    category: ProductCategory = Field(default=ProductCategory.OTHER, description="商品分类")
    related_exhibit_ids: List[str] = Field(default_factory=list, description="关联展品ID列表")
    image_urls: List[str] = Field(default_factory=list, description="商品图片URL列表")
    stock: int = Field(default=0, ge=0, description="库存数量")


class ProductCreate(ProductBase):
    """创建商品请求"""
    pass


class ProductUpdate(BaseModel):
    """更新商品请求"""
    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    price: Optional[float] = Field(default=None, gt=0)
    category: Optional[ProductCategory] = None
    related_exhibit_ids: Optional[List[str]] = None
    image_urls: Optional[List[str]] = None
    stock: Optional[int] = Field(default=None, ge=0)
    status: Optional[ProductStatus] = None


class ProductResponse(ProductBase):
    """商品响应"""
    id: str = Field(..., description="商品ID")
    status: ProductStatus = Field(default=ProductStatus.ACTIVE, description="商品状态")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")
    
    class Config:
        from_attributes = True

    @property
    def image_urls_absolute(self) -> List[str]:
        """已弃用: 请直接使用会自动转换的 image_urls"""
        return self.image_urls

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):
        """覆盖验证逻辑以处理图片 URL (兼容 ORM 和 Dict)"""
        instance = super().model_validate(obj, *args, **kwargs)
        
        # 获取基础 URL (硬编码兜底或从配置读取)
        # 注意: 在 Pydantic 模型中直接读取复杂配置可能导致循环引用，这里使用简单的环境变量或默认值
        import os
        host = os.getenv("APP_HOST", "localhost")
        port = os.getenv("APP_PORT", "8000")
        
        # 兼容处理: 0.0.0.0 无法在浏览器中直接访问，替换为 localhost
        if host == "0.0.0.0":
            host = "localhost"
            
        base_url = f"http://{host}:{port}"
        
        # 处理 image_urls
        if instance.image_urls:
            new_urls = []
            for url in instance.image_urls:
                if url and url.startswith("/static"):
                    new_urls.append(f"{base_url}{url}")
                else:
                    new_urls.append(url)
            instance.image_urls = new_urls
            
        return instance



class ProductRecommendItem(BaseModel):
    """商品推荐项"""
    id: str
    name: str
    price: float
    image_url: Optional[str] = None
    relation: str = Field(default="", description="与展品的关联说明")

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):
        """覆盖验证逻辑以处理图片 URL"""
        instance = super().model_validate(obj, *args, **kwargs)
        
        import os
        host = os.getenv("APP_HOST", "localhost")
        port = os.getenv("APP_PORT", "8000")
        
        # 兼容处理: 0.0.0.0 无法在浏览器中直接访问，替换为 localhost
        if host == "0.0.0.0":
            host = "localhost"
            
        base_url = f"http://{host}:{port}"
        
        if instance.image_url and instance.image_url.startswith("/static"):
             instance.image_url = f"{base_url}{instance.image_url}"
             
        return instance


# ========== 会话模型 ==========

class GuideSessionCreate(BaseModel):
    """创建导览会话"""
    visitor_uuid: str = Field(..., description="访客 UUID")
    person_type: Optional[PersonType] = None
    person_features: Optional[List[str]] = None
    visitor_image_path: Optional[str] = None


class GuideSessionResponse(BaseModel):
    """导览会话响应"""
    id: str
    visitor_uuid: str
    person_type: Optional[PersonType] = None
    person_features: Optional[List[str]] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# ========== 分页模型 ==========

class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    
    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginatedResponse(BaseModel):
    """分页响应"""
    items: List = Field(default_factory=list)
    total: int = Field(default=0)
    page: int = Field(default=1)
    page_size: int = Field(default=20)
    total_pages: int = Field(default=0)
