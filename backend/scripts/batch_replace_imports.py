#!/usr/bin/env python
"""
Core 模块导入路径批量替换脚本

安全执行：先预览变更，确认后再执行替换
"""
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

# 定义替换映射
IMPORT_REPLACEMENTS: Dict[str, str] = {
    # === auth 模块 -> security ===
    "from app.core.security.auth import": "from app.core.security.auth import",
    "from app.core.security.rbac_deps import": "from app.core.security.rbac_deps import",
    "from app.core.security.rbac_init import": "from app.core.security.rbac_init import",
    "from app.core.security.path_security import": "from app.core.security.path_security import",
    
    # === LLM 模块 ===
    "from app.core.llm.llm import": "from app.core.llm.llm import",
    "from app.core.llm.async_llm import": "from app.core.llm.async_llm import",
    "from app.core.llm.async_embedding import": "from app.core.llm.async_embedding import",
    "from app.core.llm.vlm_service import": "from app.core.llm.vlm_service import",
    "from app.core.llm.prompt_manager import": "from app.core.llm.prompt_manager import",
    
    # === RAG 模块 ===
    "from app.core.rag.hybrid_retriever import": "from app.core.rag.hybrid_retriever import",
    "from app.core.rag.semantic_chunker import": "from app.core.rag.semantic_chunker import",
    "from app.core.rag.reranker import": "from app.core.rag.reranker import",
    "from app.core.rag.query_rewriter import": "from app.core.rag.query_rewriter import",
    "from app.core.rag.context_expander import": "from app.core.rag.context_expander import",
    "from app.core.rag.data_context import": "from app.core.rag.data_context import",
    "from app.core.rag.summary_service import": "from app.core.rag.summary_service import",
    
    # === MCP 模块 ===
    "from app.core.mcp.mcp_client import": "from app.core.mcp.mcp_client import",
}

# 排除的目录
EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "env",
}


def find_python_files(root: str) -> List[Path]:
    """查找所有 Python 文件"""
    files = []
    for path in Path(root).rglob("*.py"):
        # 排除特定目录
        if any(exc in path.parts for exc in EXCLUDE_DIRS):
            continue
        files.append(path)
    return files


def preview_changes(files: List[Path]) -> List[Tuple[Path, List[Tuple[int, str, str]]]]:
    """预览所有变更，返回 (文件, [(行号, 原内容, 新内容), ...])"""
    all_changes = []
    
    for file_path in files:
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"无法读取 {file_path}: {e}")
            continue
        
        lines = content.split("\n")
        file_changes = []
        
        for i, line in enumerate(lines, start=1):
            for old, new in IMPORT_REPLACEMENTS.items():
                if old in line:
                    new_line = line.replace(old, new)
                    file_changes.append((i, line, new_line))
                    break  # 一行只替换一次
        
        if file_changes:
            all_changes.append((file_path, file_changes))
    
    return all_changes


def apply_changes(changes: List[Tuple[Path, List[Tuple[int, str, str]]]]) -> int:
    """应用变更，返回修改的文件数"""
    modified = 0
    
    for file_path, file_changes in changes:
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"无法读取 {file_path}: {e}")
            continue
        
        for old, new in IMPORT_REPLACEMENTS.items():
            content = content.replace(old, new)
        
        try:
            file_path.write_text(content, encoding="utf-8")
            modified += 1
            print(f"✅ 已更新: {file_path}")
        except Exception as e:
            print(f"❌ 写入失败 {file_path}: {e}")
    
    return modified


def main():
    import sys
    
    # 脚本在 scripts/ 目录下，需要找到 backend 根目录
    root = Path(__file__).parent.parent
    if not (root / "app").exists():
        # 尝试当前工作目录
        root = Path.cwd()
        if not (root / "app").exists():
            print("请在 backend 目录下运行此脚本")
            sys.exit(1)
    
    print("=" * 60)
    print("Core 模块导入路径批量替换脚本")
    print("=" * 60)
    
    # 1. 查找文件
    files = find_python_files(root)
    print(f"\n📁 扫描到 {len(files)} 个 Python 文件\n")
    
    # 2. 预览变更
    changes = preview_changes(files)
    
    if not changes:
        print("✨ 没有需要替换的内容，所有导入已是最新格式！")
        return
    
    # 3. 显示预览
    total_lines = sum(len(fc) for _, fc in changes)
    print(f"📝 预览: 将修改 {len(changes)} 个文件中的 {total_lines} 处导入\n")
    
    for file_path, file_changes in changes:
        print(f"\n📄 {file_path.relative_to(root)}")
        for line_no, old_line, new_line in file_changes[:3]:  # 最多显示3行
            print(f"   L{line_no}: {old_line.strip()}")
            print(f"      → {new_line.strip()}")
        if len(file_changes) > 3:
            print(f"   ... 还有 {len(file_changes) - 3} 处变更")
    
    # 4. 确认执行
    print("\n" + "=" * 60)
    confirm = input("是否执行替换? (y/N): ").strip().lower()
    
    if confirm == "y":
        modified = apply_changes(changes)
        print(f"\n✅ 完成！共修改 {modified} 个文件")
    else:
        print("\n❌ 已取消")


if __name__ == "__main__":
    main()
