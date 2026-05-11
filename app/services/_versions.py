"""Pure-Python firmware version comparison helpers.

Extracted from ``app/services/devices.py`` in v0.5.4 so unit tests
can import them without booting the full Flask app (which drags in
flask-limiter and other runtime deps that aren't installed on
non-container hosts).

No SQLAlchemy, no Flask, no app-context imports — exactly the
helpers, exactly testable.
"""

from __future__ import annotations


def _version_sort_key(v: str | None) -> tuple:
    """Build a sort key from a firmware version string.

    Versions look like ``0.1.5-dev-central``, ``0.1.1-dev-central-ui``,
    ``0.1.2``. We want numeric ordering by the leading dotted-int
    prefix, with the suffix as a stable tiebreaker so two releases
    with the same numeric prefix sort predictably.

    Returns a tuple ``((int, ...), suffix_str)``. ``None`` / empty
    versions sort to the very bottom.
    """
    if not v:
        return ((-1,), "")
    head, _, tail = v.partition("-")
    parts = []
    for p in head.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            # Unparsable numeric component → treat as 0; the
            # suffix tiebreaker will still keep ordering stable.
            parts.append(0)
    return (tuple(parts), tail)


def is_upgrade(target_version: str | None, current_version: str | None) -> bool:
    """v0.4.29: returns True only when ``target_version`` is
    *strictly newer* than ``current_version`` by numeric prefix.

    Used by the devices-list template to gate the one-click upgrade
    button so a device on ``0.1.5-dev-central`` never shows an
    "upgrade" button pointing at ``0.1.2-dev-central`` (a downgrade,
    which was the v0.4.21..v0.4.28 behaviour).

    Same numeric prefix → False (no button), regardless of suffix
    label. Cross-label "upgrades" are intentionally hidden to
    avoid ``0.1.1-dev-central`` → ``0.1.1-dev-central-ui`` confusion.
    """
    if not target_version or not current_version:
        return False
    return _version_sort_key(target_version)[0] > _version_sort_key(current_version)[0]
