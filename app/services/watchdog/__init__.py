"""Watchdog-rule service — public API surface.

Split into four internal modules in v0.6.48 (refactor); every
external caller imports from this package root:

- `_render.py`     — plain-English sentence renderer (pure, no DB)
- `_validate.py`   — per-kind probe + action validators
- `_query.py`      — read-only queries + serializer
- `_mutations.py`  — write operations (create / update / delete /
                     set_enabled)

See `docs/architecture.md` §"Service subpackages" for the split
rationale; mirrors the precedent set by `services/devices/`,
`services/watchdog_runtime/`, and `services/external_sensors/`.

External callers always do `from app.services.watchdog import …`,
never `from app.services.watchdog._query import …`.
"""

from __future__ import annotations

from app.services.watchdog._mutations import (
    create_rule,
    delete_rule,
    set_enabled,
    update_rule,
)
from app.services.watchdog._query import (
    _iso,
    get_rule,
    list_recent_events,
    list_rules,
    list_rules_for_device,
    probe_now,
    serialize_rule,
)
from app.services.watchdog._render import render_rule_sentence
from app.services.watchdog._validate import (
    WatchdogValidationError,
    validate_action,
    validate_probe,
)

# v0.6.48 back-compat: the legacy single-file module exposed
# `_validate_probe` / `_validate_action` / `_validate_leaf` as
# module-level callables. A handful of tests + internal callers may
# still import via those underscore names; alias them so the move is
# behavior-preserving. Promote to public names in a follow-up cleanup
# pass.
_validate_probe = validate_probe
_validate_action = validate_action

__all__ = [
    # Reads
    "list_rules",
    "list_rules_for_device",
    "list_recent_events",
    "get_rule",
    "probe_now",
    "serialize_rule",
    # Writes
    "create_rule",
    "update_rule",
    "delete_rule",
    "set_enabled",
    # Validation
    "WatchdogValidationError",
    "validate_probe",
    "validate_action",
    # Rendering
    "render_rule_sentence",
]
