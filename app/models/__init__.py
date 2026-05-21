from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from app.models.users import User  # noqa: E402,F401
from app.models.organizations import (  # noqa: E402,F401
    Organization,
    OrganizationMembership,
)
from app.models.sites import Site  # noqa: E402,F401
from app.models.devices import (  # noqa: E402,F401
    Device,
    DeviceCredential,
    DeviceHeartbeat,
    EnrollmentToken,
)
from app.models.groups import Group, GroupMembership  # noqa: E402,F401
from app.models.commands import Command, CommandResult  # noqa: E402,F401
from app.models.events import DeviceEvent  # noqa: E402,F401
from app.models.firmware import (  # noqa: E402,F401
    DeploymentAssignment,
    FirmwareDeployment,
    FirmwareRelease,
)
from app.models.invitations import Invitation  # noqa: E402,F401
from app.models.signup_requests import SignupRequest  # noqa: E402,F401
from app.models.audit import AuditEvent, AuditEventArchive  # noqa: E402,F401
from app.models.unregistered import UnregisteredAuthAttempt  # noqa: E402,F401
from app.models.sessions import Session  # noqa: E402,F401
from app.models.failsafe import DeviceFailsafeEvent  # noqa: E402,F401
from app.models.firmware_mirrors import FirmwareReleaseMirror  # noqa: E402,F401
from app.models.watchdog import WatchdogRule, WatchdogProbeEvent  # noqa: E402,F401
from app.models.password_resets import PasswordReset  # noqa: E402,F401
from app.models.runtime_flags import RuntimeFlag  # noqa: E402,F401
from app.models.schedules import Schedule  # noqa: E402,F401
from app.models.announcements import DeviceAnnouncement  # noqa: E402,F401
from app.models.attention_acks import AttentionAck  # noqa: E402,F401
from app.models.runtime_settings import RuntimeSetting  # noqa: E402,F401
from app.models.role_bindings import RoleBinding  # noqa: E402,F401
from app.models.power_analytics import DevicePowerSample  # noqa: E402,F401
from app.models.power_rollups import DevicePowerRollup  # noqa: E402,F401
from app.models.external_sensors import (  # noqa: E402,F401
    ExternalSensorSample,
    ExternalSensorSource,
)
from app.models.sync import (  # noqa: E402,F401
    OutboxEvent,
    SyncCursor,
    Tombstone,
)
from app.models.external_epg import ExternalEpgCache  # noqa: E402,F401
from app.models.scenes import Scene  # noqa: E402,F401
from app.models.notifications import (  # noqa: E402,F401
    NotificationSubscription,
    WebhookChannel,
    WebhookDelivery,
)
