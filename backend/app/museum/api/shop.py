"""
博物馆模块 - 商城 API

遵循设计原则：
- 严谨设计原则 (Design Rigor): 管理接口需管理员鉴权
- 全异步 I/O (Async First)
- 严格模式校验 (Schema Validation)
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, status, Query, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func

from app.core.db.database import get_async_db_context
from app.api.deps import get_user_context
from app.core.security.rbac_deps import CheckPerm
from app.core.security.auth import User
from app.models.common.context import UserContext
from app.museum.db import MuseumProduct
from app.museum.models import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    ProductCategory,
    PaginatedResponse,
)

logger = logging.getLogger(__name__)
get_current_admin = CheckPerm("museum:manage")


router = APIRouter(prefix="/shop", tags=["博物馆商城"])


@router.get("/template", summary="下载导入模板")
async def download_template(_: User = Depends(get_current_admin)):
    """下载商品导入 Excel 模板"""
    from app.museum.services.product_service import get_product_service
    
    product_service = get_product_service()
    output = product_service.generate_template()
    
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=product_import_template.xlsx"}
    )


@router.post("/import", summary="Excel导入商品")
async def import_products(
    file: UploadFile = File(...),
    admin: User = Depends(get_current_admin),  # 管理员鉴权
):
    """
    从 Excel 批量导入商品
    
    请先下载模板，填好后上传
    """
    from app.museum.services.product_service import get_product_service
    
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="仅支持 Excel 文件 (.xlsx, .xls)")
    
    content = await file.read()
    product_service = get_product_service()
    
    try:
        result = await product_service.import_from_excel(
            content, workspace_id=admin.workspace_id
        )
        return {"message": "导入完成", "result": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[Shop API] 导入失败: {e}")
        raise HTTPException(status_code=500, detail="导入处理失败")



@router.get("/products", response_model=PaginatedResponse)
async def list_products(
    category: Optional[ProductCategory] = Query(default=None, description="商品分类"),
    exhibit_id: Optional[str] = Query(default=None, description="关联展品ID"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user_context: UserContext = Depends(get_user_context),
):
    """
    获取商品列表
    
    支持按分类和关联展品筛选
    """
    async with get_async_db_context() as session:
        # 构建查询
        query = select(MuseumProduct).where(
            MuseumProduct.workspace_id == user_context.workspace_id,
            MuseumProduct.status == "active",
        )
        count_query = select(func.count()).select_from(MuseumProduct).where(
            MuseumProduct.workspace_id == user_context.workspace_id,
            MuseumProduct.status == "active"
        )
        
        # 分类筛选
        if category:
            query = query.where(MuseumProduct.category == category.value)
            count_query = count_query.where(MuseumProduct.category == category.value)
        
        # 关联展品筛选 (JSON 包含查询)
        if exhibit_id:
            query = query.where(
                func.json_contains(MuseumProduct.related_exhibit_ids, f'"{exhibit_id}"')
            )
            count_query = count_query.where(
                func.json_contains(MuseumProduct.related_exhibit_ids, f'"{exhibit_id}"')
            )
        
        # 统计总数
        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0
        
        # 分页
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size).order_by(MuseumProduct.created_at.desc())
        
        result = await session.execute(query)
        products = result.scalars().all()
        
        # 转换为响应模型
        # 转换为响应模型 (使用 model_validate 触发 URL 转换逻辑)
        items = []
        for p in products:
            try:
                item = ProductResponse.model_validate(p)
                items.append(item)
            except Exception as e:
                logger.error(f"Error validating product {p.id}: {e}")
                
                # 降级: 手动构造，不进行 URL 转换
                items.append(ProductResponse(
                     id=str(p.id),
                    name=p.name,
                    description=p.description,
                    price=float(p.price),
                    category=ProductCategory(p.category),
                    related_exhibit_ids=p.related_exhibit_ids or [],
                    image_urls=p.image_urls or [],
                    stock=p.stock,
                    status=p.status,
                    created_at=p.created_at,
                    updated_at=p.updated_at,
                ))
        
        total_pages = (total + page_size - 1) // page_size
        
        return PaginatedResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )


@router.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: str,
    user_context: UserContext = Depends(get_user_context),
):
    """
    获取商品详情
    """
    async with get_async_db_context() as session:
        result = await session.execute(
            select(MuseumProduct).where(
                MuseumProduct.id == product_id,
                MuseumProduct.workspace_id == user_context.workspace_id,
            )
        )
        product = result.scalar_one_or_none()
        
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"商品不存在: {product_id}"
            )
        
        return ProductResponse.model_validate(product)


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    data: ProductCreate,
    admin: User = Depends(get_current_admin),  # 管理员鉴权
):
    """
    创建商品 (管理接口，需要管理员权限)
    """
    import uuid
    
    async with get_async_db_context() as session:
        product = MuseumProduct(
            id=str(uuid.uuid4()),
            workspace_id=admin.workspace_id,
            name=data.name,
            description=data.description,
            price=data.price,
            category=data.category.value,
            related_exhibit_ids=data.related_exhibit_ids,
            image_urls=data.image_urls,
            stock=data.stock,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)
        
        logger.info(f"[Museum Shop] 创建商品: {product.id} - {product.name}")
        
        return ProductResponse.model_validate(product)


@router.put("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: str,
    data: ProductUpdate,
    admin: User = Depends(get_current_admin),  # 管理员鉴权
):
    """
    更新商品 (管理接口，需要管理员权限)
    """
    async with get_async_db_context() as session:
        result = await session.execute(
            select(MuseumProduct).where(
                MuseumProduct.id == product_id,
                MuseumProduct.workspace_id == admin.workspace_id,
            )
        )
        product = result.scalar_one_or_none()
        
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"商品不存在: {product_id}"
            )
        
        # 更新字段
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if key == "category" and value:
                value = value.value
            if key == "status" and value:
                value = value.value
            setattr(product, key, value)
        
        await session.commit()
        await session.refresh(product)
        
        logger.info(f"[Museum Shop] 更新商品: {product.id}")
        
        return ProductResponse.model_validate(product)


@router.delete("/products/{product_id}")
async def delete_product(
    product_id: str,
    admin: User = Depends(get_current_admin),  # 管理员鉴权
):
    """
    删除商品 (软删除，需要管理员权限)
    """
    async with get_async_db_context() as session:
        result = await session.execute(
            select(MuseumProduct).where(
                MuseumProduct.id == product_id,
                MuseumProduct.workspace_id == admin.workspace_id,
            )
        )
        product = result.scalar_one_or_none()
        
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"商品不存在: {product_id}"
            )
        
        product.status = "inactive"
        await session.commit()
        
        logger.info(f"[Museum Shop] 删除商品: {product_id}")
        
        return {"message": f"商品已删除: {product_id}"}


@router.post("/inquiry", summary="商品咨询")
async def product_inquiry(
    product_id: str,
    question: str,
    person_type: Optional[str] = None,
    user_context: UserContext = Depends(get_user_context),
):
    """
    商品咨询 (AI 导购)
    
    根据商品信息和用户问题生成个性化回答
    """
    from app.museum.services.product_service import get_product_service
    from app.museum.config import get_style_template, get_shop_inquiry_prompt
    from app.core.llm.async_llm import get_async_llm
    
    product_service = get_product_service()
    product = await product_service.get_product(
        product_id, workspace_id=user_context.workspace_id
    )
    
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"商品不存在: {product_id}"
        )
    
    # 获取风格配置
    style_hint = ""
    if person_type:
        style = get_style_template(person_type)
        style_hint = f"请用{style.get('tone', '友好')}的语气回答。"
    
    # 使用配置外置的 Prompt 模板
    prompt = get_shop_inquiry_prompt(
        product_name=product.name,
        product_description=product.description or '暂无描述',
        product_price=product.price,
        product_category=product.category,
        product_stock=product.stock,
        question=question,
        style_hint=style_hint,
    )
    
    try:
        llm_client = get_async_llm()
        response = await llm_client.chat([{"role": "user", "content": prompt}])
        
        logger.info(f"[Museum Shop] 商品咨询: {product_id}, 问题: {question[:50]}...")
        
        return {
            "product_id": product_id,
            "product_name": product.name,
            "question": question,
            "answer": response,
        }
    except Exception as e:
        logger.error(f"[Museum Shop] 商品咨询失败: {e}")
        return {
            "product_id": product_id,
            "product_name": product.name,
            "question": question,
            "answer": f"抱歉，我暂时无法回答您的问题。这款「{product.name}」售价 ¥{product.price}，如需更多帮助请联系工作人员。",
        }
