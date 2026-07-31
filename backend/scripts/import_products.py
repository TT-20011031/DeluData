"""
博物馆商品 Excel 导入脚本

使用方法:
    cd backend
    python scripts/import_products.py --file products.xlsx

设计原则:
    - 幂等性: 使用 UPSERT，可反复运行
    - 增量更新: 根据 product_id 或 name 更新
    - 不清空表: 保护在线用户购物车
"""

import asyncio
import argparse
import json
import logging
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from sqlalchemy import text

from app.core.db.async_engine import get_async_session

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def parse_json_field(value) -> str:
    """解析 JSON 字段"""
    if pd.isna(value) or value is None:
        return '[]'
    if isinstance(value, str):
        # 尝试解析验证 JSON 格式
        try:
            json.loads(value)
            return value
        except json.JSONDecodeError:
            # 如果不是有效 JSON，尝试按逗号分割
            items = [item.strip() for item in value.split(',') if item.strip()]
            return json.dumps(items, ensure_ascii=False)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return '[]'


def generate_product_id(name: str) -> str:
    """根据名称生成稳定的商品 ID"""
    import hashlib
    hash_value = hashlib.md5(name.encode()).hexdigest()[:8]
    return f"prod_{hash_value}"


async def import_products(file_path: str, dry_run: bool = False):
    """
    导入商品数据 (UPSERT)
    
    Args:
        file_path: Excel 文件路径
        dry_run: 如果为 True，只打印不执行
    """
    # 检查文件存在
    if not Path(file_path).exists():
        logger.error(f"文件不存在: {file_path}")
        return
    
    # 读取 Excel
    logger.info(f"读取文件: {file_path}")
    df = pd.read_excel(file_path)
    
    logger.info(f"共 {len(df)} 条记录")
    logger.info(f"列名: {list(df.columns)}")
    
    # 必需字段检查
    required_columns = ['name', 'price']
    missing = set(required_columns) - set(df.columns)
    if missing:
        logger.error(f"Excel 缺少必需列: {missing}")
        logger.info("Excel 应包含以下列: id(可选), name(必需), description, price(必需), category, related_exhibit_ids, image_urls, stock, status")
        return
    
    # UPSERT SQL
    upsert_sql = text("""
        INSERT INTO museum_products 
            (id, name, description, price, category, related_exhibit_ids, image_urls, stock, status)
        VALUES 
            (:id, :name, :description, :price, :category, :related_exhibit_ids, :image_urls, :stock, :status)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            description = VALUES(description),
            price = VALUES(price),
            category = VALUES(category),
            related_exhibit_ids = VALUES(related_exhibit_ids),
            image_urls = VALUES(image_urls),
            stock = VALUES(stock),
            status = VALUES(status),
            updated_at = CURRENT_TIMESTAMP
    """)
    
    if dry_run:
        logger.info("=== DRY RUN 模式，不执行实际写入 ===")
    
    inserted = 0
    updated = 0
    errors = 0
    
    async with get_async_session() as session:
        for idx, row in df.iterrows():
            try:
                # 构建数据
                product_id = row.get('id') if pd.notna(row.get('id')) else generate_product_id(row['name'])
                
                product_data = {
                    'id': str(product_id),
                    'name': str(row['name']),
                    'description': str(row.get('description', '')) if pd.notna(row.get('description')) else '',
                    'price': float(row['price']),
                    'category': str(row.get('category', '其他')) if pd.notna(row.get('category')) else '其他',
                    'related_exhibit_ids': parse_json_field(row.get('related_exhibit_ids')),
                    'image_urls': parse_json_field(row.get('image_urls')),
                    'stock': int(row.get('stock', 0)) if pd.notna(row.get('stock')) else 0,
                    'status': str(row.get('status', 'active')) if pd.notna(row.get('status')) else 'active'
                }
                
                if dry_run:
                    logger.info(f"[{idx + 1}] 将导入: {product_data['name']} (ID: {product_data['id']})")
                else:
                    result = await session.execute(upsert_sql, product_data)
                    
                    # rowcount: 1 = INSERT, 2 = UPDATE
                    if result.rowcount == 1:
                        inserted += 1
                        logger.debug(f"[{idx + 1}] 新增: {product_data['name']}")
                    else:
                        updated += 1
                        logger.debug(f"[{idx + 1}] 更新: {product_data['name']}")
                        
            except Exception as e:
                errors += 1
                logger.error(f"[{idx + 1}] 处理失败: {row.get('name', 'Unknown')} - {e}")
        
        if not dry_run:
            await session.commit()
    
    # 汇总
    logger.info("=" * 50)
    if dry_run:
        logger.info(f"DRY RUN 完成: 共 {len(df)} 条记录待导入")
    else:
        logger.info(f"导入完成: 新增 {inserted} 条, 更新 {updated} 条, 失败 {errors} 条")


def main():
    parser = argparse.ArgumentParser(
        description='博物馆商品 Excel 导入脚本 (UPSERT)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Excel 格式要求:
  - id: 商品ID (可选，不填则自动生成)
  - name: 商品名称 (必需)
  - description: 商品描述
  - price: 商品价格 (必需)
  - category: 分类 (文创/纪念品/仿制品/书籍/其他)
  - related_exhibit_ids: 关联展品ID (逗号分隔或 JSON 数组)
  - image_urls: 图片URL (逗号分隔或 JSON 数组)
  - stock: 库存数量
  - status: 状态 (active/inactive)

示例:
  python scripts/import_products.py --file data/products.xlsx
  python scripts/import_products.py --file data/products.xlsx --dry-run
        """
    )
    parser.add_argument('--file', required=True, help='Excel 文件路径')
    parser.add_argument('--dry-run', action='store_true', help='只预览不执行')
    
    args = parser.parse_args()
    
    asyncio.run(import_products(args.file, dry_run=args.dry_run))


if __name__ == '__main__':
    main()
