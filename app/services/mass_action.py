"""Mass-action confirmation gate.

v0.2.5: any admin action that fans out to multiple devices (group commands,
firmware deployments to a group or to all_devices) must carry a confirmation
proportional to the blast radius:

- target_count <= 5     → no confirmation required (current behavior preserved)
- 5  < target_count <= 20 → caller must supply confirmation_level="simple"
- target_count > 20      → caller must supply confirmation_level="typed" AND
                           confirmation_typed_value matching the verb the UI
                           prompted the user to type (case-sensitive)

The gate is enforced server-side. The UI provides modals that set the form
fields automatically; the API expects the same field names in the JSON body.
"""

from __future__ import annotations

from sqlalchemy import select, func

from app.db import session_scope
from app.models import Command, Device, GroupMembership

SIMPLE_THRESHOLD = 5
TYPED_THRESHOLD = 20

LEVEL_NONE = "none"
LEVEL_SIMPLE = "simple"
LEVEL_TYPED = "typed"


class ConfirmationRequired(Exception):
    """Raised when caller did not supply the confirmation required for the
    target_count."""

    def __init__(
        self,
        target_count: int,
        required_level: str,
        expected_typed_value: str | None = None,
    ):
        self.target_count = target_count
        self.required_level = required_level
        self.expected_typed_value = expected_typed_value
        msg = (
            f"mass action affects {target_count} devices and requires "
            f"confirmation_level={required_level!r}"
        )
        if expected_typed_value:
            msg += f" with confirmation_typed_value={expected_typed_value!r}"
        super().__init__(msg)


def required_level(target_count: int) -> str:
    if target_count > TYPED_THRESHOLD:
        return LEVEL_TYPED
    if target_count > SIMPLE_THRESHOLD:
        return LEVEL_SIMPLE
    return LEVEL_NONE


def validate(
    target_count: int,
    expected_typed_value: str,
    confirmation_level: str | None,
    confirmation_typed_value: str | None,
) -> None:
    """Raise ConfirmationRequired if the supplied confirmation is insufficient.

    expected_typed_value is what the caller must echo for the typed level —
    typically the command verb (e.g. "relay_cycle") or a deployment label
    (e.g. "deploy_firmware"). Comparison is case-sensitive.
    """
    needed = required_level(target_count)
    if needed == LEVEL_NONE:
        return
    if needed == LEVEL_SIMPLE:
        if confirmation_level in (LEVEL_SIMPLE, LEVEL_TYPED):
            return
        raise ConfirmationRequired(target_count, LEVEL_SIMPLE)
    # needed == LEVEL_TYPED
    if (
        confirmation_level == LEVEL_TYPED
        and confirmation_typed_value == expected_typed_value
    ):
        return
    raise ConfirmationRequired(
        target_count, LEVEL_TYPED, expected_typed_value=expected_typed_value
    )


def count_group_members(group_id: str) -> int:
    """Return the number of devices in a group. 0 if the group is unknown."""
    with session_scope() as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(GroupMembership)
                .where(GroupMembership.group_id == group_id)
            )
            or 0
        )


def count_all_active_devices() -> int:
    """Used for firmware target_type='all_devices'."""
    with session_scope() as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(Device)
                .where(Device.registration_state == "active")
            )
            or 0
        )
