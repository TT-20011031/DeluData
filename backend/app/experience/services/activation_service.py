from __future__ import annotations

import hashlib
import hmac
import secrets
import string
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.experience.models import ScienceDevice, ScienceDeviceActivationCode


def _hash_activation_key(raw_key: str) -> str:
    value = (raw_key or "").strip()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_compare(raw_key: str, expected_hash: str) -> bool:
    return hmac.compare_digest(_hash_activation_key(raw_key), expected_hash or "")


class DeviceActivationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()

    def _generate_activation_key(self) -> str:
        length = max(int(getattr(self.settings.experience, "activation_key_length", 12)), 8)
        alphabet = string.ascii_uppercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    async def _generate_device_id(self, workspace_id: str) -> str:
        while True:
            candidate = f"kiosk-{secrets.token_hex(4)}"
            result = await self.db.execute(
                select(ScienceDevice.id).where(
                    ScienceDevice.workspace_id == workspace_id,
                    ScienceDevice.device_id == candidate,
                )
            )
            if result.scalar_one_or_none() is None:
                return candidate

    async def create_activation_code(
        self,
        *,
        workspace_id: str,
        issued_by: str,
        service_user_id: str,
        dept_id: Optional[int],
        name_hint: Optional[str] = None,
    ) -> tuple[ScienceDeviceActivationCode, str]:
        while True:
            activation_key = self._generate_activation_key()
            activation_hash = _hash_activation_key(activation_key)
            result = await self.db.execute(
                select(ScienceDeviceActivationCode.id).where(
                    ScienceDeviceActivationCode.activation_key_hash == activation_hash
                )
            )
            if result.scalar_one_or_none() is None:
                break

        now = datetime.utcnow()
        code = ScienceDeviceActivationCode(
            workspace_id=workspace_id,
            name_hint=(name_hint or "").strip() or None,
            activation_key_hash=activation_hash,
            activation_key_last4=activation_key[-4:],
            service_user_id=service_user_id,
            dept_id=dept_id,
            status="pending",
            created_by=issued_by,
            created_at=now,
        )
        self.db.add(code)
        await self.db.flush()
        return code, activation_key

    async def list_activation_codes(self, workspace_id: str) -> list[ScienceDeviceActivationCode]:
        result = await self.db.execute(
            select(ScienceDeviceActivationCode)
            .where(ScienceDeviceActivationCode.workspace_id == workspace_id)
            .order_by(ScienceDeviceActivationCode.created_at.desc())
        )
        return list(result.scalars().all())

    async def activate_device(
        self,
        *,
        activation_key: str,
        workspace_id: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> ScienceDevice:
        del device_id
        activation_hash = _hash_activation_key(activation_key)
        result = await self.db.execute(
            select(ScienceDeviceActivationCode)
            .where(ScienceDeviceActivationCode.activation_key_hash == activation_hash)
            .with_for_update()
        )
        code = result.scalar_one_or_none()
        if code is None:
            raise HTTPException(status_code=401, detail="activation_key_invalid")
        if workspace_id and code.workspace_id != workspace_id:
            raise HTTPException(status_code=401, detail="activation_key_invalid")
        if code.status == "used":
            raise HTTPException(status_code=409, detail="activation_key_used")
        if code.status != "pending":
            raise HTTPException(status_code=403, detail="activation_key_revoked")
        if not _safe_compare(activation_key, code.activation_key_hash):
            raise HTTPException(status_code=401, detail="activation_key_invalid")

        new_device_id = await self._generate_device_id(code.workspace_id)
        now = datetime.utcnow()
        device = ScienceDevice(
            workspace_id=code.workspace_id,
            device_id=new_device_id,
            name=code.name_hint or f"Kiosk {new_device_id[-4:].upper()}",
            device_token=secrets.token_urlsafe(32),
            activation_key_hash=None,
            activation_key_last4=code.activation_key_last4,
            activation_updated_at=now,
            service_user_id=code.service_user_id,
            dept_id=code.dept_id,
            kb_scope_mode="files",
            kb_scope_dept_ids_json=[],
            kb_scope_file_ids_json=[],
            is_active=True,
        )
        self.db.add(device)
        await self.db.flush()

        code.status = "used"
        code.used_device_id = device.device_id
        code.used_at = now
        await self.db.flush()
        return device
