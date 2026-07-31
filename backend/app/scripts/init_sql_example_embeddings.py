"""Rebuild SQL example embeddings using the explicit external embedding pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.db.database import get_async_db_manager
from app.models.config.sql_example_embeddings import sync_all_sql_examples_to_vector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild SQL example embeddings.")
    parser.add_argument("--workspace-id", default=None, help="Only rebuild one workspace.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear existing vectors before rebuilding. Recommended when migrating from the old Chroma default model.",
    )
    parser.add_argument("--output", default=None, help="Write JSON report to this file.")
    return parser.parse_args()


async def _main() -> None:
    args = parse_args()
    summary = await sync_all_sql_examples_to_vector(
        workspace_id=args.workspace_id,
        reset=bool(args.reset),
    )

    output = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
        print(f"report saved: {output_path}")
    else:
        print(output)

    await get_async_db_manager().dispose()


if __name__ == "__main__":
    asyncio.run(_main())
