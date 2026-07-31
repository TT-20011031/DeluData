"""
DataFrame 工具函数

提供 DataFrame 数据清洗和导出功能
"""
import io
import math

import pandas as pd


def clean_dataframe_for_json(df: pd.DataFrame) -> pd.DataFrame:
    """
    清洗 DataFrame，处理 NaN/Infinity 值
    
    避免 JSON 序列化时崩溃
    
    Args:
        df: 原始 DataFrame
        
    Returns:
        清洗后的 DataFrame 副本
    """
    df_clean = df.copy()
    for col in df_clean.select_dtypes(include=['float', 'float64']).columns:
        df_clean[col] = df_clean[col].apply(
            lambda x: None if (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else x
        )
    return df_clean


def export_dataframe_to_csv(df: pd.DataFrame) -> io.StringIO:
    """
    导出 DataFrame 为 CSV 格式
    
    使用 utf-8-sig 编码，解决 Excel 打开中文乱码问题
    
    Args:
        df: DataFrame 数据
        
    Returns:
        包含 CSV 内容的 StringIO 对象
    """
    buffer = io.StringIO()
    df.to_csv(buffer, index=False, encoding='utf-8-sig')
    buffer.seek(0)
    return buffer


def export_dataframe_to_excel(df: pd.DataFrame) -> io.BytesIO:
    """
    导出 DataFrame 为 Excel 格式
    
    Args:
        df: DataFrame 数据
        
    Returns:
        包含 Excel 内容的 BytesIO 对象
    """
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine='openpyxl')
    buffer.seek(0)
    return buffer


def slice_dataframe(df: pd.DataFrame, max_rows: int = 500) -> tuple[pd.DataFrame, int, int]:
    """
    限制 DataFrame 返回行数
    
    Args:
        df: 原始 DataFrame
        max_rows: 最大行数
        
    Returns:
        (切片后的 DataFrame, 总行数, 显示行数) 元组
    """
    data_slice = df.head(max_rows)
    return data_slice, len(df), len(data_slice)
