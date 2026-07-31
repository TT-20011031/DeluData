"""
博物馆目录服务
负责处理"冷启动/宽泛查询"的目录线索(Seeding)和"全局文创"推荐(Global Recommendations)
"""
import logging
import random
from typing import List, Optional

from sqlalchemy import select, func, desc
from app.core.db.database import get_async_db_context
from app.core.db.tenant_mixin import get_current_workspace
from app.museum.db import MuseumArtifact, MuseumProduct
from app.museum.models import PersonType, ProductRecommendItem

logger = logging.getLogger(__name__)

class CatalogService:
    """目录服务"""

    def _resolve_workspace(self, workspace_id: Optional[str] = None) -> str:
        return workspace_id or get_current_workspace() or "default"
    
    async def get_catalog_seeds(
        self,
        limit: int = 3,
        workspace_id: Optional[str] = None,
    ) -> List[str]:
        """
        获取镇馆之宝线索 (Seeding)
        
        用于宽泛查询时注入到 Prompt 中，强制 RAG 召回这些文物
        """
        workspace = self._resolve_workspace(workspace_id)
        try:
            async with get_async_db_context() as session:
                # 优先按热度(click_count)降序，其次随机
                stmt = (
                    select(MuseumArtifact.name)
                    .where(
                        MuseumArtifact.workspace_id == workspace,
                        MuseumArtifact.is_highlight == True,
                    )
                    .order_by(desc(MuseumArtifact.click_count), func.random())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                names = result.scalars().all()
                
                logger.info(f"[CatalogService] Retrieved seeds: {names}")
                return list(names)
        except Exception as e:
            logger.error(f"[CatalogService] 获取目录线索失败: {e}", exc_info=True)
            return []

    async def get_global_products(
        self, 
        person_type: PersonType, 
        limit: int = 3,
        workspace_id: Optional[str] = None,
    ) -> List[ProductRecommendItem]:
        """
        获取全局文创推荐 (针对人群优化)
        
        策略（已修复 BUG2）：
        1. 获取所有 status=Active 的商品（移除 is_global 限制）
        2. 按人群匹配度排序：
           - 强匹配: target_crowd 包含 person_type (score=0)
           - 弱匹配: target_crowd 包含 "通用访客" (GENERAL) 或为空/None (score=1)
           - 其他: 不匹配但仍可推荐 (score=2)
        3. 返回 Top N（随机打乱同级商品）
        """
        workspace = self._resolve_workspace(workspace_id)
        try:
            async with get_async_db_context() as session:
                stmt = (
                    select(MuseumProduct)
                    .where(
                        MuseumProduct.workspace_id == workspace,
                        MuseumProduct.status == "active",
                    )
                )
                result = await session.execute(stmt)
                all_products = result.scalars().all()
                
                if not all_products:
                    logger.warning("[CatalogService] 数据库中无 active 商品")
                    return []
                
                # 类型安全转换：统一转为 str (支持 Enum/str 输入)
                def to_person_type_str(pt) -> str:
                    """将 PersonType Enum 或 str 统一转为 str"""
                    if isinstance(pt, PersonType):
                        return pt.value
                    if isinstance(pt, str):
                        try:
                            return PersonType(pt).value
                        except ValueError:
                            logger.warning(f"[CatalogService] Invalid person_type str: {pt}, using GENERAL")
                            return PersonType.GENERAL.value
                    logger.warning(f"[CatalogService] Unknown person_type type: {type(pt)}, using GENERAL")
                    return PersonType.GENERAL.value
                
                pt_str = to_person_type_str(person_type)
                general_str = PersonType.GENERAL.value
                
                # Python 层排序 (数据量小，逻辑灵活)
                scored_products = []
                for p in all_products:
                    targets = p.target_crowd or []
                    
                    if pt_str in targets:
                        score = 0  # 精确匹配角色
                    elif general_str in targets or not targets:
                        score = 1  # 通用匹配（target_crowd 包含 GENERAL 或为空/None）
                    else:
                        score = 2  # 不匹配但仍可兜底推荐
                        
                    scored_products.append((score, p))
                
                # 排序：先按 score 升序，同级随机打乱
                random.shuffle(scored_products)
                scored_products.sort(key=lambda x: x[0])
                
                # 选出 Top N
                final_products = []
                for score, p in scored_products:
                    if len(final_products) >= limit:
                        break
                    item = self._convert_to_item(p, relation="为您推荐")
                    final_products.append(item)
                
                logger.info(f"[CatalogService] 角色推荐: person_type={pt_str}, 返回 {len(final_products)} 个商品")
                return final_products
                
        except Exception as e:
            logger.error(f"[CatalogService] 获取全局推荐失败: {e}", exc_info=True)
            return []


    def _convert_to_item(self, product: MuseumProduct, relation: str = "精选文创") -> ProductRecommendItem:
        """模型转换
        
        Args:
            product: 商品 ORM 对象
            relation: 推荐关系描述，用于区分推荐来源
        """
        from app.museum.config import get_museum_settings
        settings = get_museum_settings()
        base_url = settings.image_base_url
        
        img_url = None
        if product.image_urls:
            raw = product.image_urls[0]
            if raw and raw.startswith("/static"):
                img_url = f"{base_url}{raw}"
            else:
                img_url = raw
                
        return ProductRecommendItem(
            id=product.id,
            name=product.name,
            price=float(product.price),
            image_url=img_url,
            relation=relation
        )

# 单例工厂
_catalog_service: Optional[CatalogService] = None

def get_catalog_service() -> CatalogService:
    global _catalog_service
    if _catalog_service is None:
        _catalog_service = CatalogService()
    return _catalog_service
