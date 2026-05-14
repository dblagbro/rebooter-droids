"""Devices service — public API surface.

Split into three internal modules in v0.5.15 (refactor); every external
caller imports from this package root:

- `_serialize.py` — pure dict/presentation helpers
- `_query.py`     — read-only queries
- `_mutations.py` — write operations

See `docs/architecture.md` §"Source layout" for the split rationale.
"""

from __future__ import annotations

from app.services.devices._mutations import (
    UnknownPatchFieldError,
    delete_device,
    delete_devices_bulk,
    enqueue_display_name_sync,
    update_device,
)
from app.services.devices._query import (
    _active_assignments_by_device,
    _latest_heartbeat_by_device,
    find_by_mac,
    firmware_version_breakdown,
    get_device_detail,
    latest_stable_release_dict,
    list_devices,
)
from app.services.devices._serialize import (
    _derive_central_status,
    _heartbeat_state_for,
    _iso,
    _serialize_assignment,
    serialize_device,
)

# v0.5.4: re-exported from app/services/_versions.py for back-compat
# with existing callers (templates' is_upgrade Jinja global, blueprint imports).
from app.services._versions import _version_sort_key, is_upgrade  # noqa: F401

__all__ = [
    # Serialization
    "serialize_device",
    # Reads
    "find_by_mac",
    "latest_stable_release_dict",
    "firmware_version_breakdown",
    "list_devices",
    "get_device_detail",
    # Writes
    "UnknownPatchFieldError",
    "delete_device",
    "delete_devices_bulk",
    "update_device",
    "enqueue_display_name_sync",
    # Version helpers (re-exported from _versions.py)
    "is_upgrade",
    "_version_sort_key",
]
