from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.db import session_scope
from app.models import FirmwareRelease

ALLOWED_CHANNELS = ("dev", "beta", "stable")


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def serialize_release(r: FirmwareRelease) -> dict:
    return {
        "id": r.id,
        "version": r.version,
        "channel": r.channel,
        "filename": r.filename,
        "download_url": r.download_url,
        "sha256": r.sha256,
        "size_bytes": r.size_bytes,
        "release_notes": r.release_notes,
        "created_at": _iso(r.created_at),
    }


def list_releases() -> list[dict]:
    with session_scope() as session:
        rows = list(session.scalars(select(FirmwareRelease).order_by(FirmwareRelease.created_at.desc())))
        return [serialize_release(r) for r in rows]


def _sha256_of_path(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def upload_release(
    settings: Settings,
    upload_stream,
    version: str,
    channel: str,
    expected_sha256: str | None,
    release_notes: str | None,
    issued_by_user_id: str | None,
) -> dict:
    if channel not in ALLOWED_CHANNELS:
        raise ValueError(f"channel must be one of {ALLOWED_CHANNELS}")
    version = version.strip()
    if not version:
        raise ValueError("version is required")

    firmware = Path(settings.firmware_dir)
    firmware.mkdir(parents=True, exist_ok=True)
    # Stage in the firmware dir itself so the rename is on the same filesystem.
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".upload-", dir=str(firmware))
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            shutil.copyfileobj(upload_stream, f)
        actual_sha = _sha256_of_path(tmp_path)
        if expected_sha256 and expected_sha256.lower() != actual_sha:
            raise ValueError(
                f"sha256 mismatch: expected {expected_sha256.lower()}, got {actual_sha}"
            )

        size = os.path.getsize(tmp_path)
        final_name = f"rebooter-{version}.bin"
        if channel != "stable":
            final_name = f"rebooter-{version}-{channel}.bin"
        final_path = firmware / final_name
        if final_path.exists():
            raise ValueError(
                f"firmware {final_name} already exists; bump version or delete first"
            )

        os.replace(tmp_path, final_path)
        try:
            os.chmod(final_path, 0o644)
        except OSError:
            pass
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    download_url = f"{settings.firmware_public_base.rstrip('/')}/{final_name}"

    record = FirmwareRelease(
        version=version,
        channel=channel,
        filename=final_name,
        download_url=download_url,
        sha256=actual_sha,
        size_bytes=size,
        release_notes=release_notes,
        created_by_user_id=issued_by_user_id,
        created_at=datetime.now(timezone.utc),
    )
    with session_scope() as session:
        session.add(record)
        session.flush()
        out = serialize_release(record)
    return out


def delete_release(release_id: str, settings: Settings) -> bool:
    with session_scope() as session:
        r = session.get(FirmwareRelease, release_id)
        if r is None:
            return False
        firmware = Path(settings.firmware_dir) / r.filename
        if firmware.exists():
            firmware.unlink()
        session.delete(r)
        session.flush()
        return True
