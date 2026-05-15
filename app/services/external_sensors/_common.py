"""Shared leaf helpers for the external_sensors subpackage.

Imported by `_crud`, `_pollers`, `_inbound`, and `_query`. Keep this
dependency-free (stdlib only) so it never participates in an import
cycle. See `app/services/external_sensors/__init__.py` for the package
overview.
"""

from __future__ import annotations

from datetime import datetime

# Roku ECP default port — used both by `_crud.create_source` (default
# port for a roku source) and `_pollers._poll_roku`.
ROKU_DEFAULT_PORT = 8060


def _iso(dt: datetime | None) -> str | None:
    """UTC datetime → `YYYY-MM-DDTHH:MM:SSZ`, or None."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None
