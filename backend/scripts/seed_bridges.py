import asyncio
import os
import sys
import logging
from sqlalchemy import text, select

# 添加项目根目录到 Python 路径
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.core.db.database import get_async_db_context, get_async_db_manager, Base
from app.museum.db import MuseumArtifact, MuseumProduct

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 文物列表 (The Bridge Anchors)
ARTIFACTS_DATA = [
    "贾湖骨笛", "妇好鸮尊", "玉柄铁剑", "莲鹤方壶", "武则天金简", 
    "杜岭方鼎", "云纹铜禁", "四神云气图壁画", "汝窑天蓝釉刻花鹅颈瓶", 
    "三彩童子傀儡戏枕", "三彩荷叶童子枕", "带翼铜铃", "金缕玉衣", 
    "彩陶双连壶", "龙耳虎足方壶", "蟠螭纹铜盖鼎", "七层连阁彩绘陶楼", 
    "象尊", "三彩黑釉马", "王孙诰编钟", "隋白瓷龙柄双连瓶", 
    "战国石排箫", "唐代绞胎瓷枕", "汉代彩绘陶斗拱", "原始青瓷尊", 
    "兽面纹铜平底爵", "汉代陶水榭", "越王不光剑", "唐三彩文吏俑", 
    "钧窑天蓝釉红斑花瓣式碗", "汉代错金银铜豹", "兽面纹铜铙", 
    "仰韶文化彩陶钵", "龙形佩", "汉代彩绘陶狗", "宋代白釉黑花瓷瓶"
]

# 模拟的文创商品数据 (Business Data)
# 格式: (ArtifactName, ProductName, Price, Description, Category, ImagePath)
PRODUCTS_DATA = [
    ("贾湖骨笛", "骨笛造型银哨", 128.00, "以贾湖骨笛为原型的纯银哨子，声音清越", "纪念品", "/static/products/gudi_whistle.png"),
    ("妇好鸮尊", "鸮尊Q版手办", 58.00, "呆萌可爱的猫头鹰尊公仔，桌面摆件", "文创", "/static/products/xiaozun_figure.png"),
    ("莲鹤方壶", "仙鹤祥云书签", 25.00, "精美金属书签，提取莲鹤方壶顶部的飞鹤元素", "文创", "/static/products/crane_bookmark.png"),
    ("云纹铜禁", "云纹镂空杯垫", 36.00, "复刻云纹铜禁的复杂镂空工艺，隔热防滑", "生活用品", "/static/products/bronze_coaster.png"),
    ("四神云气图壁画", "四神云气真丝丝巾", 299.00, "取材于汉墓壁画，色彩绚丽，寓意吉祥", "服饰", "/static/products/cloud_scarf.png"),
    ("金缕玉衣", "玉衣片拼图", 45.00, "益智拼图，重现金缕玉衣的辉煌", "玩具", "/static/products/jade_puzzle.png"),
    ("唐三彩文吏俑", "唐风文官笔记本", 18.00, "封面印有唐三彩文官像，内页复古纸张", "办公", "/static/products/tang_notebook.png"),
    ("三彩黑釉马", "黑马骑士毛绒玩偶", 66.00, "柔软舒适的黑马玩偶，深受孩子喜爱", "玩具", "/static/products/black_horse_plush.png"),
]

async def seed_data():
    """播种数据：桥接表 + 商品表"""
    
    # 1. 确保表存在
    logger.info("正在初始化数据库表结构...")
    manager = get_async_db_manager()
    async with manager.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with get_async_db_context() as session:
        try:
            # 2. 清理旧数据 (Artifacts)
            logger.info("清理旧数据...")
            # 注意：先删商品，再删文物，因为有外键关联 (虽然这里用了 JSON 弱关联，但逻辑上为了整洁)
            # 这里我们不硬删商品，只清空 museum_artifacts 表重新建立索引
            await session.execute(text("TRUNCATE TABLE museum_artifacts"))
            
            # 3. 播种文物 (Bridge)
            logger.info(f"正在播种 {len(ARTIFACTS_DATA)} 个文物索引...")
            artifact_map = {}  # Name -> ID
            
            for name in ARTIFACTS_DATA:
                artifact = MuseumArtifact(name=name)
                session.add(artifact)
                # Flush 以获取 ID
                await session.flush() 
                artifact_map[name] = artifact.id
                logger.debug(f"Created Artifact: {name} ID={artifact.id}")
            
            # 4. 播种商品 (Products) 并关联
            logger.info("正在播种测试文创商品...")
            
            for link_name, prod_name, price, desc, cat, image_path in PRODUCTS_DATA:
                artifact_id = artifact_map.get(link_name)
                if not artifact_id:
                    logger.warning(f"找不到文物 {link_name} 的ID，跳过商品 {prod_name}")
                    continue
                
                # 检查是否存在同名商品，避免重复
                existing = await session.execute(
                    select(MuseumProduct).where(MuseumProduct.name == prod_name)
                )
                existing_product = existing.scalar_one_or_none()
                if existing_product:
                    logger.info(f"商品 {prod_name} 已存在，更新关联与图片...")
                    existing_product.related_exhibit_ids = [str(artifact_id)]
                    existing_product.image_urls = [image_path] if image_path else []
                    session.add(existing_product)
                    continue

                product = MuseumProduct(
                    name=prod_name,
                    description=desc,
                    price=price,
                    category="文创", # 简化分类
                    related_exhibit_ids=[str(artifact_id)], # 关键：写入 Bridge ID
                    status="active",
                    stock=100,
                    image_urls=[image_path] if image_path else []
                )
                session.add(product)
                logger.info(f"Created Product: {prod_name} -> Linked to {link_name}(ID={artifact_id})")

            await session.commit()
            logger.info("✅ 数据播种完成！")
            
        except Exception as e:
            logger.error(f"播种失败: {e}")
            await session.rollback()
            raise
        finally:
            # 必须从管理器获取引擎并关闭，否则 Windows 下会报 Event loop is closed
            manager = get_async_db_manager()
            await manager.dispose()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(seed_data())
