"""
博物馆模块 - 商品服务

遵循设计原则：
- 严谨设计原则 (Design Rigor): 模块化解耦
- 全异步 I/O (Async First): 所有数据库操作异步
- 严格模式校验 (Schema Validation): Pydantic 数据验证

功能：
- 商品 CRUD 操作
- 基于展品的商品推荐
- AI 商品咨询
"""

import logging
from typing import Optional, List
import io
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from sqlalchemy import select, delete, and_, or_

from app.core.db.database import get_async_db_context
from app.core.db.tenant_mixin import get_current_workspace
from app.museum.db import MuseumProduct
from app.museum.models import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    ProductRecommendItem,
    ProductCategory,
    ProductStatus,
)

logger = logging.getLogger(__name__)


class ProductService:
    """
    商品服务
    
    提供商品 CRUD 和智能推荐功能
    """
    
    def _resolve_workspace(self, workspace_id: Optional[str] = None) -> str:
        return workspace_id or get_current_workspace() or "default"

    async def list_products(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        workspace_id: Optional[str] = None,
    ) -> tuple[List[ProductResponse], int]:
        """
        获取商品列表
        
        Args:
            category: 商品分类过滤
            status: 商品状态过滤
            limit: 返回数量限制
            offset: 偏移量
            
        Returns:
            (商品列表, 总数)
        """
        workspace = self._resolve_workspace(workspace_id)

        async with get_async_db_context() as session:
            # 构建查询条件
            conditions = [MuseumProduct.workspace_id == workspace]
            if category:
                conditions.append(MuseumProduct.category == category)
            if status:
                conditions.append(MuseumProduct.status == status)
            
            # 查询总数
            count_query = select(MuseumProduct)
            if conditions:
                count_query = count_query.where(and_(*conditions))
            result = await session.execute(count_query)
            total = len(result.scalars().all())
            
            # 查询列表
            query = select(MuseumProduct).limit(limit).offset(offset).order_by(
                MuseumProduct.created_at.desc()
            )
            if conditions:
                query = query.where(and_(*conditions))
            
            result = await session.execute(query)
            products = result.scalars().all()
            
            return [self._to_response(p) for p in products], total
    
    async def get_product(
        self,
        product_id: str,
        workspace_id: Optional[str] = None,
    ) -> Optional[ProductResponse]:
        """
        获取单个商品
        
        Args:
            product_id: 商品ID
            
        Returns:
            商品信息或 None
        """
        workspace = self._resolve_workspace(workspace_id)

        async with get_async_db_context() as session:
            result = await session.execute(
                select(MuseumProduct).where(
                    MuseumProduct.id == product_id,
                    MuseumProduct.workspace_id == workspace,
                )
            )
            product = result.scalar_one_or_none()
            return self._to_response(product) if product else None
    
    async def create_product(
        self,
        data: ProductCreate,
        workspace_id: Optional[str] = None,
    ) -> ProductResponse:
        """
        创建商品
        
        Args:
            data: 商品创建数据
            
        Returns:
            创建的商品
        """
        workspace = self._resolve_workspace(workspace_id)

        async with get_async_db_context() as session:
            product = MuseumProduct(
                workspace_id=workspace,
                name=data.name,
                description=data.description,
                price=float(data.price),
                category=data.category,
                related_exhibit_ids=data.related_exhibit_ids,
                image_urls=data.image_urls,
                stock=data.stock,
                status="active",
            )
            session.add(product)
            await session.commit()
            await session.refresh(product)
            
            logger.info(f"[ProductService] 创建商品: {product.id} - {product.name}")
            return self._to_response(product)
    
    async def update_product(
        self, 
        product_id: str, 
        data: ProductUpdate,
        workspace_id: Optional[str] = None,
    ) -> Optional[ProductResponse]:
        """
        更新商品
        
        Args:
            product_id: 商品ID
            data: 更新数据
            
        Returns:
            更新后的商品或 None
        """
        workspace = self._resolve_workspace(workspace_id)

        async with get_async_db_context() as session:
            # 检查商品是否存在
            result = await session.execute(
                select(MuseumProduct).where(
                    MuseumProduct.id == product_id,
                    MuseumProduct.workspace_id == workspace,
                )
            )
            product = result.scalar_one_or_none()
            if not product:
                return None
            
            # 更新字段
            update_data = data.model_dump(exclude_unset=True)
            if update_data:
                for key, value in update_data.items():
                    if hasattr(product, key):
                        setattr(product, key, value)
                
                await session.commit()
                await session.refresh(product)
                logger.info(f"[ProductService] 更新商品: {product_id}")
            
            return self._to_response(product)
    
    async def delete_product(
        self,
        product_id: str,
        workspace_id: Optional[str] = None,
    ) -> bool:
        """
        删除商品
        
        Args:
            product_id: 商品ID
            
        Returns:
            是否删除成功
        """
        workspace = self._resolve_workspace(workspace_id)

        async with get_async_db_context() as session:
            result = await session.execute(
                delete(MuseumProduct).where(
                    MuseumProduct.id == product_id,
                    MuseumProduct.workspace_id == workspace,
                )
            )
            await session.commit()
            
            deleted = result.rowcount > 0
            if deleted:
                logger.info(f"[ProductService] 删除商品: {product_id}")
            return deleted
    
    async def get_products_by_exhibit(
        self, 
        exhibit_id: str,
        limit: int = 5,
        workspace_id: Optional[str] = None,
    ) -> List[ProductRecommendItem]:
        """
        根据展品ID获取关联商品（用于导览推荐）
        
        Args:
            exhibit_id: 展品ID
            limit: 返回数量限制
            
        Returns:
            推荐商品列表
        """
        workspace = self._resolve_workspace(workspace_id)

        async with get_async_db_context() as session:
            # 查询关联该展品的商品
            query = select(MuseumProduct).where(
                and_(
                    MuseumProduct.workspace_id == workspace,
                    MuseumProduct.status == "active",
                    MuseumProduct.stock > 0,
                )
            ).limit(limit * 2)  # 多查一些用于筛选
            
            result = await session.execute(query)
            products = result.scalars().all()
            
            # 筛选包含该展品ID的商品
            matched = []
            for p in products:
                if p.related_exhibit_ids and exhibit_id in p.related_exhibit_ids:
                    matched.append(p)
                    if len(matched) >= limit:
                        break
            
            return [
                ProductRecommendItem(
                    id=p.id,
                    name=p.name,
                    price=float(p.price),
                    image_url=p.image_urls[0] if p.image_urls else None,
                    reason=f"与您正在了解的展品相关",
                )
                for p in matched
            ]
    
    async def search_products(
        self,
        keyword: str,
        category: Optional[str] = None,
        limit: int = 10,
        workspace_id: Optional[str] = None,
    ) -> List[ProductResponse]:
        """
        搜索商品（支持拆词模糊匹配）
        
        Args:
            keyword: 搜索关键词
            category: 分类过滤
            limit: 返回数量限制
            
        Returns:
            匹配的商品列表
        """
        import jieba
        
        workspace = self._resolve_workspace(workspace_id)

        async with get_async_db_context() as session:
            # 拆词：将长关键词拆分为多个短词
            keywords = list(jieba.cut(keyword))
            # 过滤停用词和太短的词
            keywords = [w for w in keywords if len(w) >= 2 and w not in ("文创", "周边", "商品", "产品", "纪念品")]
            
            if not keywords:
                keywords = [keyword]  # 兜底：使用原始关键词
            
            # 构建 OR 条件：任意一个词匹配即可
            keyword_conditions = []
            for kw in keywords:
                keyword_conditions.append(MuseumProduct.name.ilike(f"%{kw}%"))
                keyword_conditions.append(MuseumProduct.description.ilike(f"%{kw}%"))
            
            conditions = [
                or_(*keyword_conditions),
                MuseumProduct.workspace_id == workspace,
                MuseumProduct.status == "active",
            ]
            
            if category:
                conditions.append(MuseumProduct.category == category)
            
            query = select(MuseumProduct).where(
                and_(*conditions)
            ).limit(limit)
            
            result = await session.execute(query)
            products = result.scalars().all()
            
            return [self._to_response(p) for p in products]
    
    async def upsert_product(
        self,
        data: ProductCreate,
        workspace_id: Optional[str] = None,
    ) -> ProductResponse:
        """
        UPSERT 商品（按名称查找，存在则更新，不存在则创建）
        
        用于数据导入场景
        
        Args:
            data: 商品数据
            
        Returns:
            商品信息
        """
        workspace = self._resolve_workspace(workspace_id)

        async with get_async_db_context() as session:
            # 按名称查找
            result = await session.execute(
                select(MuseumProduct).where(
                    MuseumProduct.name == data.name,
                    MuseumProduct.workspace_id == workspace,
                )
            )
            product = result.scalar_one_or_none()
            
            if product:
                # 更新
                product.description = data.description
                product.price = float(data.price)
                product.category = data.category
                product.related_exhibit_ids = data.related_exhibit_ids
                product.image_urls = data.image_urls
                product.stock = data.stock
                
                await session.commit()
                await session.refresh(product)
                logger.info(f"[ProductService] UPSERT 更新商品: {product.id}")
            else:
                # 创建
                product = MuseumProduct(
                    workspace_id=workspace,
                    name=data.name,
                    description=data.description,
                    price=float(data.price),
                    category=data.category,
                    related_exhibit_ids=data.related_exhibit_ids,
                    image_urls=data.image_urls,
                    stock=data.stock,
                    status="active",
                )
                session.add(product)
                await session.commit()
                await session.refresh(product)
                logger.info(f"[ProductService] UPSERT 创建商品: {product.id}")
            
            return self._to_response(product)
    

    def generate_template(self) -> io.BytesIO:
        """生成商品导入模板"""
        wb = Workbook()
        ws = wb.active
        ws.title = "商品导入模板"
        
        # 表头
        headers = ["商品名称", "描述", "价格", "分类", "关联文物名称(多个用逗号分隔)", "库存", "状态"]
        ws.append(headers)
        
        # 样式
        header_fill = PatternFill(start_color="CCE5FF", end_color="CCE5FF", fill_type="solid")
        header_font = Font(bold=True)
        
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            
        # 示例数据
        ws.append([
            "示例商品-骨笛书签", "精美的骨笛造型金属书签", 25.0, "文创", "贾湖骨笛", 100, "active"
        ])
        
        # 列宽
        ws.column_dimensions['A'].width = 20
        ws.column_dimensions['B'].width = 30
        ws.column_dimensions['E'].width = 30
        
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output

    async def import_from_excel(
        self,
        file_content: bytes,
        workspace_id: Optional[str] = None,
    ) -> dict:
        """从 Excel 导入商品"""
        try:
            workspace = self._resolve_workspace(workspace_id)
            df = pd.read_excel(io.BytesIO(file_content))
            
            # 兼容列名（防止用户修改模板导致匹配失败）
            column_map = {
                "商品名称": "name", 
                "描述": "description", 
                "价格": "price", 
                "分类": "category",
                "库存": "stock",
                "状态": "status",
                "关联文物名称(多个用逗号分隔)": "link_names"
            }
            df = df.rename(columns=column_map)
            
            results = {"success": 0, "failed": 0, "errors": []}
            
            async with get_async_db_context() as session:
                from app.museum.db import MuseumArtifact
                
                # 预加载所有文物名称做映射
                artifacts = (
                    await session.execute(
                        select(MuseumArtifact).where(
                            MuseumArtifact.workspace_id == workspace
                        )
                    )
                ).scalars().all()
                artifact_map = {a.name: str(a.id) for a in artifacts}
                
                for index, row in df.iterrows():
                    try:
                        row_num = index + 2  # Excel 行号
                        name = str(row.get('name', '')).strip()
                        if not name: continue
                        
                        # 解析关联ID
                        link_names = str(row.get('link_names', '')).replace('，', ',').split(',')
                        link_ids = []
                        for ln in link_names:
                            ln = ln.strip()
                            if ln in artifact_map:
                                link_ids.append(artifact_map[ln])
                        
                        # 构造数据
                        data = ProductCreate(
                            name=name,
                            description=str(row.get('description', '')),
                            price=float(row.get('price', 0)),
                            category=row.get('category', '其他'),
                            related_exhibit_ids=link_ids, # 转换为 Bridge IDs
                            stock=int(row.get('stock', 0)),
                            image_urls=[]
                        )
                        
                        await self.upsert_product(data, workspace_id=workspace)
                        results["success"] += 1
                        
                    except Exception as e:
                        results["failed"] += 1
                        results["errors"].append(f"第 {row_num} 行导入失败: {str(e)}")
                        
            return results
            
        except Exception as e:
            logger.error(f"[ProductService] Excel 导入失败: {e}")
            raise ValueError(f"文件解析失败: {str(e)}")

    def _to_response(self, product: MuseumProduct) -> ProductResponse:
        """转换为响应模型"""
        return ProductResponse(
            id=product.id,
            name=product.name,
            description=product.description,
            price=float(product.price),
            category=product.category,
            related_exhibit_ids=product.related_exhibit_ids or [],
            image_urls=product.image_urls or [],
            stock=product.stock,
            status=product.status,
            created_at=product.created_at,
            updated_at=product.updated_at,
        )


# ========== 单例工厂 ==========

_product_service: Optional[ProductService] = None


def get_product_service() -> ProductService:
    """获取商品服务单例"""
    global _product_service
    if _product_service is None:
        _product_service = ProductService()
    return _product_service
