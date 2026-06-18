"""Picker-scope re-validation helper — common defense against the
BUG-064 / BUG-068 / BUG-074 form-tamper vector.

When a handler renders a `<select>` (or any other constrained input)
from a server-side list, the SUBMIT side must re-validate the
operator's chosen value against the same constraint. A naive handler
that trusts `request.form.get('target_id')` accepts ANY id — form
tamper or a stale dropdown becomes a cross-scope RBAC bypass.

This module standardizes the defense:

  1. The render-time handler computes the visible set the same way
     the picker did (same filters, same scope).
  2. The submit-time handler calls `validate_picker_id(form_value,
     visible_ids, scope_label="Power source")`.
  3. `validate_picker_id` returns the id when it's in the visible
     set, returns `None` when the form value is empty and not
     required, or raises `PickerScopeError` with a
     ready-to-flash operator-facing message.

Active call sites (v0.6.49):

- `blueprints/admin/devices_ui.py::device_update_submit` —
  power_source_device_id (BUG-064 / BUG-068).
- `blueprints/admin/schedules.py::schedules_create_submit` —
  target_id (BUG-074).
- `blueprints/admin/rules.py::rules_create_submit` +
  `rules_update_submit` — target_id (closes the same vector on the
  watchdog-rule picker; was open).
- `blueprints/admin/groups.py::group_add_member_submit` — device_id
  (consistency; admin-only today but the same shape).

Each call site still computes its own visible set (the SCOPE differs
per picker — devices_ui scopes by site, schedules picks from the
unfiltered fleet, etc.). The helper centralizes only the
"is this id in the set?" gate + the error contract.
"""

from __future__ import annotations

from typing import Iterable


class PickerScopeError(ValueError):
    """Raised when a form-submitted id is not in the picker's visible
    set. The handler catches and flashes — never lets the value reach
    the service layer where the FK would either silently no-op or
    bubble an IntegrityError to a 500."""

    def __init__(self, scope_label: str, submitted: str | None) -> None:
        msg = (
            f"{scope_label} could not be set — that selection is not in "
            f"your visible fleet."
        )
        super().__init__(msg)
        self.scope_label = scope_label
        self.submitted = submitted


def validate_picker_id(
    form_value: str | None,
    visible_ids: Iterable[str],
    *,
    scope_label: str,
    required: bool = True,
) -> str | None:
    """Validate a form-submitted picker id against its visible set.

    Args:
        form_value: the raw value from `request.form.get(...)`.
            Already-stripped is fine; this helper strips defensively.
        visible_ids: the set of ids the picker rendered as options.
            Iterable so callers can pass a `set`, list, or generator.
        scope_label: short noun used in the error message
            (e.g. "Power source", "Schedule target", "Group member").
            Will be capitalised in the rendered flash.
        required: when False, an empty form value returns None instead
            of raising. Use for optional pickers (e.g. clearing the
            power_source_device_id is legitimate).

    Returns:
        The validated id (stripped) when present and visible, or None
        when the input was empty and `required=False`.

    Raises:
        PickerScopeError: when the input is non-empty and not in
            visible_ids, OR when the input is empty and `required=True`.
    """
    value = (form_value or "").strip()
    if not value:
        if required:
            raise PickerScopeError(scope_label, None)
        return None
    # Iterables (e.g. a generator) get materialized lazily on the `in`
    # check; converting up-front lets us short-circuit cheaply and
    # avoids exhausting a one-shot generator on a callers' second use.
    visible = visible_ids if isinstance(visible_ids, (set, frozenset)) else set(visible_ids)
    if value not in visible:
        raise PickerScopeError(scope_label, value)
    return value
