"""Write operations on watchdog rules (create / update / delete /
set_enabled).

Split from the legacy single-file `services/watchdog.py` in v0.6.48.
Validation lives in `_validate.py` and the post-write serializer in
`_query.py`; this module owns the actual session mutations.

External callers MUST import via `app.services.watchdog`, never this
module directly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.db import session_scope
from app.models import WatchdogRule
from app.models.watchdog import (
    KNOWN_PROBE_KINDS,
    RULE_STATUS_ARMED,
    RULE_STATUS_DISABLED,
)

from app.services.watchdog._query import serialize_rule
from app.services.watchdog._render import render_rule_sentence
from app.services.watchdog._validate import (
    WatchdogValidationError,
    validate_action,
    validate_probe,
)


def _validate_rule_inputs(
    *,
    name: str,
    probe: dict,
    target: dict,
    action: dict,
    failure_threshold: int,
    recovery_threshold: int,
    window_seconds: int,
    cooldown_seconds: int,
) -> str:
    """Shared pre-flight for create_rule + update_rule. Pre-fix the
    same block was duplicated verbatim across both. Returns the
    normalized name (stripped); raises WatchdogValidationError on any
    failure so the session never opens."""
    name = (name or "").strip()
    if not name:
        raise WatchdogValidationError("name is required")
    # v0.4.11 (BUG-036): cap to column width before insert.
    if len(name) > 120:
        raise WatchdogValidationError("name must be 120 characters or fewer")
    if not isinstance(probe, dict) or probe.get("kind") not in KNOWN_PROBE_KINDS:
        raise WatchdogValidationError(
            f"probe.kind must be one of {KNOWN_PROBE_KINDS}"
        )
    # v0.5.34 (BUG-055 fix): per-kind probe-field validation. Replaces
    # the v0.5.9 internet-only inline validator with a full dispatcher
    # covering all 13 canonical kinds.
    validate_probe(probe)
    if not isinstance(target, dict) or target.get("kind") not in (
        "device", "group", "tag"
    ):
        raise WatchdogValidationError(
            "target.kind must be 'device' | 'group' | 'tag'"
        )
    # v0.4.12 (BUG-038): require a concrete identifier per kind.
    # device/group → `id`; tag → `tag`. Without this the runtime
    # silently no-ops because _resolve_target_devices returns [].
    if target["kind"] in ("device", "group") and not (target.get("id") or "").strip():
        raise WatchdogValidationError(
            f"target.id is required when target.kind={target['kind']!r}"
        )
    if target["kind"] == "tag" and not (target.get("tag") or "").strip():
        raise WatchdogValidationError("target.tag is required when target.kind='tag'")
    validate_action(action)
    # v0.4.11 (BUG-035): bound the numeric thresholds so the runtime
    # state machine has well-defined behavior. Without this:
    # - failure_threshold <= 0 makes every failure fire immediately
    #   (the `failure_streak < failure_threshold` gate is False on
    #   the first probe).
    # - window_seconds < 1 makes the rule eligible every tick.
    # - cooldown_seconds < 0 turns cooldown off.
    # All of those are footguns; reject in the service.
    if int(failure_threshold) < 1 or int(failure_threshold) > 100:
        raise WatchdogValidationError("failure_threshold must be between 1 and 100")
    if int(recovery_threshold) < 1 or int(recovery_threshold) > 100:
        raise WatchdogValidationError("recovery_threshold must be between 1 and 100")
    if int(window_seconds) < 5 or int(window_seconds) > 86400:
        raise WatchdogValidationError("window_seconds must be between 5 and 86400 (1 day)")
    if int(cooldown_seconds) < 0 or int(cooldown_seconds) > 86400:
        raise WatchdogValidationError("cooldown_seconds must be between 0 and 86400 (1 day)")
    return name


def create_rule(
    *,
    name: str,
    probe: dict,
    target: dict,
    action: dict,
    site_id: str | None = None,
    description: str | None = None,
    failure_threshold: int = 3,
    recovery_threshold: int = 2,
    window_seconds: int = 60,
    cooldown_seconds: int = 300,
    max_retries: int = 3,
    retry_delay_seconds: int = 60,
    escalation: dict | None = None,
    maintenance_windows: list | None = None,
    created_by_user_id: str | None = None,
) -> dict:
    name = _validate_rule_inputs(
        name=name,
        probe=probe,
        target=target,
        action=action,
        failure_threshold=failure_threshold,
        recovery_threshold=recovery_threshold,
        window_seconds=window_seconds,
        cooldown_seconds=cooldown_seconds,
    )

    rule = WatchdogRule(
        site_id=site_id,
        name=name,
        description=(description or None),
        enabled=True,
        status=RULE_STATUS_ARMED,
        probe=probe,
        failure_threshold=int(failure_threshold),
        recovery_threshold=int(recovery_threshold),
        window_seconds=int(window_seconds),
        cooldown_seconds=int(cooldown_seconds),
        target=target,
        action=action,
        max_retries=int(max_retries),
        retry_delay_seconds=int(retry_delay_seconds),
        escalation=escalation or {"kind": "stop"},
        maintenance_windows=maintenance_windows or [],
        created_by_user_id=created_by_user_id,
    )
    with session_scope() as session:
        session.add(rule)
        session.flush()
        return serialize_rule(rule, sentence=render_rule_sentence(rule))


def update_rule(
    rule_id: str,
    *,
    name: str,
    probe: dict,
    target: dict,
    action: dict,
    site_id: str | None = None,
    description: str | None = None,
    failure_threshold: int = 3,
    recovery_threshold: int = 2,
    window_seconds: int = 60,
    cooldown_seconds: int = 300,
    max_retries: int = 3,
    retry_delay_seconds: int = 60,
    escalation: dict | None = None,
    maintenance_windows: list | None = None,
    updated_by_user_id: str | None = None,
) -> dict | None:
    """v0.5.19 (Rules UX phase): full-rule update. Same validation
    surface as create_rule. Resets the runtime state-machine counters
    (failure_streak / recovery_streak / status='armed') so a rule that
    was stuck firing comes back clean after a config change.

    Returns None if rule_id doesn't exist; otherwise the freshly-
    serialized rule dict.
    """
    name = _validate_rule_inputs(
        name=name,
        probe=probe,
        target=target,
        action=action,
        failure_threshold=failure_threshold,
        recovery_threshold=recovery_threshold,
        window_seconds=window_seconds,
        cooldown_seconds=cooldown_seconds,
    )

    with session_scope() as session:
        rule = session.get(WatchdogRule, rule_id)
        if rule is None:
            return None
        rule.name = name
        rule.description = description or None
        rule.site_id = site_id
        rule.probe = probe
        rule.target = target
        rule.action = action
        rule.failure_threshold = int(failure_threshold)
        rule.recovery_threshold = int(recovery_threshold)
        rule.window_seconds = int(window_seconds)
        rule.cooldown_seconds = int(cooldown_seconds)
        rule.max_retries = int(max_retries)
        rule.retry_delay_seconds = int(retry_delay_seconds)
        rule.escalation = escalation or {"kind": "stop"}
        rule.maintenance_windows = maintenance_windows or []
        # Reset runtime state so the operator's config change takes
        # effect cleanly. A rule that was mid-firing comes back armed;
        # next probe starts the streak counters from zero.
        rule.failure_streak = 0
        rule.recovery_streak = 0
        if rule.enabled and rule.status != RULE_STATUS_DISABLED:
            rule.status = RULE_STATUS_ARMED
        rule.updated_at = datetime.now(timezone.utc)
        session.add(rule)
        session.flush()
        return serialize_rule(rule, sentence=render_rule_sentence(rule))


def delete_rule(rule_id: str) -> bool:
    with session_scope() as session:
        r = session.get(WatchdogRule, rule_id)
        if r is None:
            return False
        session.delete(r)
        session.flush()
        return True


def set_enabled(rule_id: str, enabled: bool) -> bool:
    with session_scope() as session:
        r = session.get(WatchdogRule, rule_id)
        if r is None:
            return False
        r.enabled = bool(enabled)
        r.status = RULE_STATUS_ARMED if enabled else RULE_STATUS_DISABLED
        r.updated_at = datetime.now(timezone.utc)
        session.add(r)
        session.flush()
        return True
