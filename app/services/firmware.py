from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db import session_scope
from app.models import FirmwareRelease, FirmwareReleaseMirror
from app.models.firmware_mirrors import (
    MIRROR_KIND_LOCAL,
    MIRROR_STATUS_LIVE,
)

ALLOWED_CHANNELS = ("dev", "beta", "stable")
# v0.3.9 (RFC-002 P1): the special channel-pointer filename. Each
# channel's `latest.bin` is overwritten on every upload so that
# operators (and the bootstrap firmware per RFC-005) can fetch
# `<channel>/latest.bin` without knowing the exact version.
CHANNEL_POINTER_FILENAME = "latest.bin"


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def serialize_release(r: FirmwareRelease, mirrors: list[dict] | None = None) -> dict:
    """v0.3.9: optional `mirrors` argument carries the
    per-(release, kind) FirmwareReleaseMirror rows so the admin
    UI can show the mirror status table per release."""
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
        "mirrors": mirrors or [],
    }


def _serialize_mirror(m: FirmwareReleaseMirror) -> dict:
    return {
        "id": m.id,
        "kind": m.kind,
        "url": m.url,
        "status": m.status,
        "verified_sha256": m.verified_sha256,
        "last_probed_at": _iso(m.last_probed_at),
        "last_error": m.last_error,
        "created_at": _iso(m.created_at),
    }


def list_releases() -> list[dict]:
    with session_scope() as session:
        rows = list(session.scalars(select(FirmwareRelease).order_by(FirmwareRelease.created_at.desc())))
        # Per-release mirror rows.
        mirrors_by_release: dict[str, list[dict]] = {}
        for m in session.scalars(select(FirmwareReleaseMirror)):
            mirrors_by_release.setdefault(m.release_id, []).append(_serialize_mirror(m))
        return [
            serialize_release(r, mirrors=mirrors_by_release.get(r.id, []))
            for r in rows
        ]


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
        if size == 0:
            raise ValueError("uploaded firmware is empty (0 bytes)")
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

    # v0.3.9 (RFC-002 P1): also publish under the per-channel
    # subdirectory and update the channel-pointer file. Operator-
    # facing URL stays at the flat layout for backwards-compat with
    # devices already in the field; the per-channel paths are the
    # new surface RFC-005 needs for the bootstrap-pull-latest flow.
    channel_dir = firmware / channel
    channel_dir.mkdir(parents=True, exist_ok=True)
    # Explicit chmod: container default umask creates dirs as 0o750
    # which blocks the nginx container's user from traversing in.
    # 0o755 matches the parent firmware_dir's mode.
    try:
        os.chmod(channel_dir, 0o755)
    except OSError:
        pass
    per_channel_path = channel_dir / final_name
    try:
        # Copy (don't symlink) for Docker-bind-mount portability.
        shutil.copyfile(final_path, per_channel_path)
        os.chmod(per_channel_path, 0o644)
    except OSError:
        # Per-channel publish is best-effort in v0.3.9; the canonical
        # flat-layout file is the source of truth. Log but don't fail
        # the upload.
        import logging
        logging.getLogger(__name__).exception(
            "per-channel publish failed for %s", final_name
        )

    base = settings.firmware_public_base.rstrip("/")
    download_url = f"{base}/{final_name}"
    per_channel_url = f"{base}/{channel}/{final_name}"
    # v0.3.9: the channel pointer is a Flask 302-redirect endpoint,
    # NOT a static file. Static `latest.bin` files would collide
    # with nginx's open_file_cache (a global 60s inode cache).
    # The redirect queries the DB on every hit, so it's always
    # fresh. The public-base URL is /rebooter/firmware/<file>; we
    # derive the API root by swapping the trailing /firmware for
    # /api/v1/firmware. This is a thin string transformation; if
    # the operator changes the public-base shape, the contract
    # needs updating in lockstep.
    api_root = base
    if base.endswith("/firmware"):
        api_root = base[: -len("/firmware")] + "/api/v1/firmware"
    pointer_url = f"{api_root}/{channel}/latest"

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
    try:
        with session_scope() as session:
            session.add(record)
            session.flush()
            release_id = record.id
            # v0.3.9: record local-mirror rows for the canonical, per-channel,
            # and channel-pointer URLs. Each is serving the same SHA-256;
            # status=live since we just wrote them.
            now = datetime.now(timezone.utc)
            for kind, url in (
                (MIRROR_KIND_LOCAL, download_url),
                (f"{MIRROR_KIND_LOCAL}_per_channel", per_channel_url),
                (f"{MIRROR_KIND_LOCAL}_channel_pointer", pointer_url),
            ):
                session.add(FirmwareReleaseMirror(
                    release_id=release_id,
                    kind=kind,
                    url=url,
                    status=MIRROR_STATUS_LIVE,
                    verified_sha256=actual_sha,
                    last_probed_at=now,
                    created_at=now,
                ))
            session.flush()
            mirrors_serialized = [
                _serialize_mirror(m)
                for m in session.scalars(
                    select(FirmwareReleaseMirror)
                    .where(FirmwareReleaseMirror.release_id == release_id)
                )
            ]
            out = serialize_release(record, mirrors=mirrors_serialized)
    except IntegrityError:
        # A concurrent upload claimed the same (version, channel) first.
        # Clean up the firmware blobs we already wrote.
        # BUG-002a (v0.4.4): v0.3.9 dropped the static channel-pointer
        # file (it's a Flask redirect now). Removing the now-undefined
        # `pointer_path` from this cleanup loop — was raising NameError
        # → 500 on the loser thread of a concurrent upload race.
        for p in (final_path, per_channel_path):
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        raise ValueError(
            f"firmware {final_name} already exists; bump version or delete first"
        )
    return out


def discover_on_disk_releases(
    settings: Settings,
    *,
    issued_by_user_id: str | None = None,
) -> dict:
    """v0.4.19 (Tier-1 B): backfill `firmware_releases` rows for any
    `.bin` files that exist under `data/firmware/<channel>/` but
    are missing from the DB.

    Use case: the firmware team places artifacts directly on disk
    (e.g. via SCP or a CI/CD pipeline) without going through
    `POST /api/v1/admin/firmware/releases`. nginx serves them fine
    but the admin UI's `/app/firmware` page can't see them and the
    DB-driven channel-pointer redirect at
    `/api/v1/firmware/<channel>/latest` returns 404.

    This scan walks the channel sub-directories, computes SHA-256
    + size for each `.bin`, and inserts a row + mirror records for
    anything not already tracked. Skips:
      - the `latest.bin` channel-pointer files (they're copies of
        a real versioned artifact already accounted for)
      - files whose filename is already in `firmware_releases`
      - the `bootstrap/` directory (bootstrap firmware uses a
        different lifecycle and lives under
        `firmware_public_base/bootstrap/...` directly)

    Returns `{discovered: [...], skipped_existing: int,
    skipped_pointer: int, errors: [...]}`.
    """
    firmware = Path(settings.firmware_dir)
    base = settings.firmware_public_base.rstrip("/")
    api_root = base
    if base.endswith("/firmware"):
        api_root = base[: -len("/firmware")] + "/api/v1/firmware"

    discovered: list[dict] = []
    skipped_existing = 0
    skipped_pointer = 0
    errors: list[dict] = []

    # Pull existing filenames in one query.
    with session_scope() as session:
        existing = {
            r.filename
            for r in session.scalars(select(FirmwareRelease))
        }

    now = datetime.now(timezone.utc)
    for channel in ALLOWED_CHANNELS:
        channel_dir = firmware / channel
        if not channel_dir.is_dir():
            continue
        for entry in sorted(channel_dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.name == CHANNEL_POINTER_FILENAME:
                skipped_pointer += 1
                continue
            if not entry.name.endswith(".bin"):
                continue
            if entry.name in existing:
                skipped_existing += 1
                continue
            try:
                sha = _sha256_of_path(str(entry))
                size = entry.stat().st_size
                # Try to extract a version from the filename pattern
                # `rebooter-<version>-<channel>.bin` or
                # `rebooter-<version>.bin`.
                stem = entry.stem  # without .bin
                version = stem
                if stem.startswith("rebooter-"):
                    version = stem[len("rebooter-"):]
                # Strip a trailing `-<channel>` if it matches
                if version.endswith(f"-{channel}"):
                    version = version[: -(len(channel) + 1)]
                download_url = f"{base}/{entry.name}"
                per_channel_url = f"{base}/{channel}/{entry.name}"
                pointer_url = f"{api_root}/{channel}/latest"

                with session_scope() as session:
                    record = FirmwareRelease(
                        version=version,
                        channel=channel,
                        filename=entry.name,
                        download_url=download_url,
                        sha256=sha,
                        size_bytes=size,
                        release_notes="discovered by on-disk scan",
                        created_by_user_id=issued_by_user_id,
                        created_at=now,
                    )
                    session.add(record)
                    session.flush()
                    rid = record.id
                    for kind, url in (
                        (MIRROR_KIND_LOCAL, download_url),
                        (f"{MIRROR_KIND_LOCAL}_per_channel", per_channel_url),
                        (f"{MIRROR_KIND_LOCAL}_channel_pointer", pointer_url),
                    ):
                        session.add(FirmwareReleaseMirror(
                            release_id=rid,
                            kind=kind,
                            url=url,
                            status=MIRROR_STATUS_LIVE,
                            verified_sha256=sha,
                            last_probed_at=now,
                            created_at=now,
                        ))
                    session.flush()

                discovered.append({
                    "id": rid,
                    "version": version,
                    "channel": channel,
                    "filename": entry.name,
                    "size_bytes": size,
                    "sha256": sha,
                })
            except IntegrityError:
                # Race: another worker discovered it concurrently.
                skipped_existing += 1
            except Exception as e:
                errors.append({
                    "path": str(entry),
                    "error": f"{type(e).__name__}: {e}",
                })

    return {
        "discovered": discovered,
        "skipped_existing": skipped_existing,
        "skipped_pointer": skipped_pointer,
        "errors": errors,
    }


def delete_release(release_id: str, settings: Settings) -> bool:
    """Hard-delete the FirmwareRelease + its FirmwareReleaseMirror
    rows (cascade) + on-disk artifacts (canonical + per-channel).
    The channel pointer is a Flask redirect, so it self-updates."""
    with session_scope() as session:
        r = session.get(FirmwareRelease, release_id)
        if r is None:
            return False
        firmware = Path(settings.firmware_dir)
        canonical = firmware / r.filename
        per_channel = firmware / r.channel / r.filename
        for p in (canonical, per_channel):
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        session.delete(r)
        session.flush()
        return True


def latest_in_channel(channel: str) -> FirmwareRelease | None:
    """v0.3.9: backing query for the channel-pointer redirect endpoint.
    Returns the most-recently-created release in this channel, or None."""
    if channel not in ALLOWED_CHANNELS:
        return None
    with session_scope() as session:
        r = session.scalar(
            select(FirmwareRelease)
            .where(FirmwareRelease.channel == channel)
            .order_by(FirmwareRelease.created_at.desc())
            .limit(1)
        )
        if r is not None:
            session.expunge(r)
        return r
