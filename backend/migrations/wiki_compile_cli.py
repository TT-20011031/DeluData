"""Wiki 编译 / Lint CLI 入口（M1 验收用）。

用法：
    cd backend
    # 1) 全量编译某个工作区（取已索引文件）
    python -m migrations.wiki_compile_cli compile --workspace-id ws-1 --scope workspace

    # 2) 仅编译指定 file_id（多个用空格分隔）
    python -m migrations.wiki_compile_cli compile --workspace-id ws-1 --file-ids abc123 def456

    # 3) 跑一遍健康度巡检
    python -m migrations.wiki_compile_cli lint --workspace-id ws-1

    # 4) 列出某个工作区已有实体页
    python -m migrations.wiki_compile_cli list --workspace-id ws-1

环境变量：
    WIKI_COMPILE_MODEL_CREATE / WIKI_COMPILE_MODEL_UPDATE / DASHSCOPE_API_KEY 等
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.wiki_service import get_wiki_service  # noqa: E402


def _print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


async def cmd_compile(args: argparse.Namespace) -> None:
    service = get_wiki_service()
    triggered_by = f"cli:{args.user or 'admin'}"
    if args.scope == "workspace":
        result = await service.compile_workspace(
            workspace_id=args.workspace_id,
            triggered_by=triggered_by,
            user_id=args.user,
            file_limit=args.limit,
        )
    else:
        if not args.file_ids:
            print("[ERROR] 必须提供 --file-ids 或 --scope workspace")
            sys.exit(2)
        result = await service.compile_files(
            workspace_id=args.workspace_id,
            file_ids=list(args.file_ids),
            triggered_by=triggered_by,
            user_id=args.user,
        )
    _print_json(result)


async def cmd_lint(args: argparse.Namespace) -> None:
    service = get_wiki_service()
    report = await service.lint_workspace(workspace_id=args.workspace_id)
    _print_json(report)


async def cmd_list(args: argparse.Namespace) -> None:
    service = get_wiki_service()
    data = await service.list_pages(
        workspace_id=args.workspace_id,
        domain=args.domain,
        status=args.status,
        keyword=args.keyword,
        limit=args.limit,
        offset=0,
    )
    _print_json(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DeluData Wiki Compile/Lint CLI"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # compile
    p_compile = sub.add_parser("compile", help="编译指定 workspace 的实体页")
    p_compile.add_argument("--workspace-id", required=True)
    p_compile.add_argument("--scope", choices=["workspace"], default=None)
    p_compile.add_argument("--file-ids", nargs="*", default=[])
    p_compile.add_argument("--user", default=None, help="user_id（仅记录用）")
    p_compile.add_argument("--limit", type=int, default=200, help="scope=workspace 时最多取多少文件")
    p_compile.set_defaults(func=cmd_compile)

    # lint
    p_lint = sub.add_parser("lint", help="对 workspace 跑一次健康度巡检")
    p_lint.add_argument("--workspace-id", required=True)
    p_lint.set_defaults(func=cmd_lint)

    # list
    p_list = sub.add_parser("list", help="列出 workspace 的实体页")
    p_list.add_argument("--workspace-id", required=True)
    p_list.add_argument("--domain", default=None)
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--keyword", default=None)
    p_list.add_argument("--limit", type=int, default=50)
    p_list.set_defaults(func=cmd_list)

    return parser


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    await args.func(args)


if __name__ == "__main__":
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
