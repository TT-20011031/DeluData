"""Migrate persisted local files into OSS and rewrite DB paths."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from app.config import get_settings
from app.core.db.database import get_async_db_context
from app.core.storage.service import get_storage_service
from app.core.utils.storage_path import is_oss_path, normalize_storage_path, resolve_storage_path
from app.models.config.template import TemplateModel
from app.models.knowledge.graph import DocumentImage, File


async def _migrate_files(*, workspace_id: Optional[str], dry_run: bool) -> tuple[int, int]:
    storage_service = get_storage_service()
    migrated = 0
    skipped = 0

    async with get_async_db_context() as session:
        stmt = select(File)
        if workspace_id:
            stmt = stmt.where(File.workspace_id == workspace_id)
        result = await session.execute(stmt)
        rows = result.scalars().all()

        for row in rows:
            current_path = str(row.storage_path or "").strip()
            if not current_path or is_oss_path(current_path):
                skipped += 1
                continue

            local_path = Path(resolve_storage_path(current_path))
            if not local_path.exists():
                skipped += 1
                continue

            filename = local_path.name or f"{row.id}.{row.file_type or 'bin'}"
            object_key = storage_service.build_document_object_key(str(row.workspace_id), str(row.id), filename)
            if not dry_run:
                row.storage_path = normalize_storage_path(
                    await storage_service.upload_file(str(local_path), object_key)
                )
            migrated += 1

        if not dry_run:
            await session.commit()

    return migrated, skipped


async def _migrate_document_images(*, workspace_id: Optional[str], dry_run: bool) -> tuple[int, int]:
    storage_service = get_storage_service()
    migrated = 0
    skipped = 0

    async with get_async_db_context() as session:
        stmt = (
            select(DocumentImage, File.workspace_id)
            .join(File, File.id == DocumentImage.file_id)
        )
        if workspace_id:
            stmt = stmt.where(File.workspace_id == workspace_id)
        result = await session.execute(stmt)
        rows = result.all()

        for image, owner_workspace_id in rows:
            current_path = str(image.storage_path or "").strip()
            if not current_path or is_oss_path(current_path):
                skipped += 1
                continue

            local_path = Path(resolve_storage_path(current_path))
            if not local_path.exists():
                skipped += 1
                continue

            filename = local_path.name or f"{image.image_id}.png"
            object_key = storage_service.build_document_image_object_key(
                str(owner_workspace_id),
                str(image.file_id),
                filename,
            )
            if not dry_run:
                image.storage_path = normalize_storage_path(
                    await storage_service.upload_file(str(local_path), object_key, content_type="image/png")
                )
            migrated += 1

        if not dry_run:
            await session.commit()

    return migrated, skipped


async def _migrate_templates(*, workspace_id: Optional[str], dry_run: bool) -> tuple[int, int]:
    storage_service = get_storage_service()
    migrated = 0
    skipped = 0

    async with get_async_db_context() as session:
        stmt = select(TemplateModel)
        if workspace_id:
            stmt = stmt.where(TemplateModel.workspace_id == workspace_id)
        result = await session.execute(stmt)
        rows = result.scalars().all()

        for row in rows:
            current_path = str(row.file_path or "").strip()
            if not current_path or is_oss_path(current_path):
                skipped += 1
                continue

            local_path = Path(resolve_storage_path(current_path))
            if not local_path.exists():
                skipped += 1
                continue

            filename = local_path.name or f"{row.id}.{row.file_type or 'bin'}"
            object_key = storage_service.build_template_object_key(
                str(row.workspace_id),
                str(row.id),
                filename,
            )
            if not dry_run:
                row.file_path = normalize_storage_path(
                    await storage_service.upload_file(str(local_path), object_key)
                )
            migrated += 1

        if not dry_run:
            await session.commit()

    return migrated, skipped


async def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate persisted local files into OSS.")
    parser.add_argument("--workspace-id", default=None, help="Only migrate one workspace")
    parser.add_argument("--dry-run", action="store_true", help="Scan and count only")
    args = parser.parse_args()

    settings = get_settings()
    if settings.storage.backend.lower() != "oss":
        raise SystemExit("STORAGE_BACKEND must be oss before running this script.")
    if not settings.oss.is_configured:
        raise SystemExit("OSS credentials are not fully configured.")

    file_migrated, file_skipped = await _migrate_files(
        workspace_id=args.workspace_id,
        dry_run=args.dry_run,
    )
    image_migrated, image_skipped = await _migrate_document_images(
        workspace_id=args.workspace_id,
        dry_run=args.dry_run,
    )
    template_migrated, template_skipped = await _migrate_templates(
        workspace_id=args.workspace_id,
        dry_run=args.dry_run,
    )

    print(
        {
            "dry_run": bool(args.dry_run),
            "workspace_id": args.workspace_id,
            "files": {"migrated": file_migrated, "skipped": file_skipped},
            "document_images": {"migrated": image_migrated, "skipped": image_skipped},
            "templates": {"migrated": template_migrated, "skipped": template_skipped},
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
