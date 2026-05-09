"""Watchdog-rule service — v0.4.0 (P4 first slice).

CRUD only. No probe runtime yet — that's v0.4.1+.

The plain-English `render_rule_sentence()` helper is the canonical
rule representation per webui-redesign-requirements.md R-WD-1:

    "If <probe> fails <failure-threshold> times over <window>,
     <action> on <target>, then wait <recovery-delay> and check
     <recovery-threshold> successes before re-arming."

In v0.4.0 the rule editor is a simple form; the plain-English
sentence is rendered read-only on the list page so operators see
the human-readable shape immediately.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import Device, Group, Site, WatchdogRule
from app.models.watchdog import (
    KNOWN_PROBE_KINDS,
    PROBE_KIND_PING,
    RULE_STATUS_ARMED,
    RULE_STATUS_DISABLED,
)


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def serialize_rule(r: WatchdogRule, sentence: str | None = None) -> dict:
    return {
        "id": r.id,
        "site_id": r.site_id,
        "name": r.name,
        "description": r.description,
        "enabled": bool(r.enabled),
        "status": r.status,
        "probe": r.probe or {},
        "failure_threshold": r.failure_threshold,
        "recovery_threshold": r.recovery_threshold,
        "window_seconds": r.window_seconds,
        "cooldown_seconds": r.cooldown_seconds,
        "target": r.target or {},
        "action": r.action or {},
        "max_retries": r.max_retries,
        "retry_delay_seconds": r.retry_delay_seconds,
        "escalation": r.escalation or {},
        "maintenance_windows": r.maintenance_windows or [],
        "created_at": _iso(r.created_at),
        "updated_at": _iso(r.updated_at),
        "sentence": sentence,
    }


def list_rules(site_id: str | None = None) -> list[dict]:
    with session_scope() as session:
        stmt = select(WatchdogRule).order_by(WatchdogRule.created_at.desc())
        if site_id:
            stmt = stmt.where(WatchdogRule.site_id == site_id)
        rules = list(session.scalars(stmt))
        # Resolve target names eagerly so the sentence renderer
        # doesn't need its own DB pass per row.
        device_names = {
            d.id: d.display_name or d.id
            for d in session.scalars(select(Device))
        }
        group_names = {
            g.id: g.name for g in session.scalars(select(Group))
        }
        out = []
        for r in rules:
            sentence = render_rule_sentence(
                r, device_names=device_names, group_names=group_names
            )
            out.append(serialize_rule(r, sentence=sentence))
        return out


def get_rule(rule_id: str) -> dict | None:
    with session_scope() as session:
        r = session.get(WatchdogRule, rule_id)
        if r is None:
            return None
        sentence = render_rule_sentence(r)
        return serialize_rule(r, sentence=sentence)


class WatchdogValidationError(ValueError):
    pass


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
    name = (name or "").strip()
    if not name:
        raise WatchdogValidationError("name is required")
    if not isinstance(probe, dict) or probe.get("kind") not in KNOWN_PROBE_KINDS:
        raise WatchdogValidationError(
            f"probe.kind must be one of {KNOWN_PROBE_KINDS}"
        )
    if not isinstance(target, dict) or target.get("kind") not in (
        "device", "group", "tag"
    ):
        raise WatchdogValidationError(
            "target.kind must be 'device' | 'group' | 'tag'"
        )
    if not isinstance(action, dict) or action.get("kind") not in (
        "cycle", "hold_off", "notify_only"
    ):
        raise WatchdogValidationError(
            "action.kind must be 'cycle' | 'hold_off' | 'notify_only'"
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


# ── plain-English sentence renderer (R-WD-1) ─────────────────────────────

def render_rule_sentence(
    rule: WatchdogRule,
    *,
    device_names: dict[str, str] | None = None,
    group_names: dict[str, str] | None = None,
) -> str:
    """Produce the human-readable sentence shape:
        "If <probe> fails <failure-threshold> times over <window>,
         <action> on <target>, then wait <recovery-delay> and
         check <recovery-threshold> successes before re-arming."
    """
    probe = rule.probe or {}
    probe_str = _probe_to_phrase(probe)

    target = rule.target or {}
    target_str = _target_to_phrase(
        target,
        device_names=device_names or {},
        group_names=group_names or {},
    )

    action = rule.action or {}
    action_str = _action_to_phrase(action)

    win_str = _seconds_to_phrase(rule.window_seconds)
    cool_str = _seconds_to_phrase(rule.cooldown_seconds)

    return (
        f"If {probe_str} fails {rule.failure_threshold} consecutive times "
        f"over {win_str}, {action_str} on {target_str}, then wait {cool_str} "
        f"and check {rule.recovery_threshold} successes before re-arming."
    )


def _probe_to_phrase(p: dict) -> str:
    k = p.get("kind", "?")
    if k == "internet":
        return "outbound internet connectivity"
    if k == "ping":
        return f"ping to `{p.get('host','?')}`"
    if k == "tcp":
        return f"TCP connect to `{p.get('host','?')}:{p.get('port','?')}`"
    if k == "http":
        return f"HTTP GET to `{p.get('url','?')}`"
    if k == "dns":
        return f"DNS resolve `{p.get('hostname','?')}`"
    if k == "gateway":
        return "ping to the device's LAN gateway"
    if k == "custom":
        return f"custom probe `{p.get('name','?')}`"
    return f"unknown probe '{k}'"


def _target_to_phrase(
    t: dict,
    *,
    device_names: dict[str, str],
    group_names: dict[str, str],
) -> str:
    k = t.get("kind")
    if k == "device":
        did = t.get("id", "?")
        return f"device `{device_names.get(did, did)}`"
    if k == "group":
        gid = t.get("id", "?")
        return f"group `{group_names.get(gid, gid)}`"
    if k == "tag":
        return f"any device tagged `{t.get('tag','?')}`"
    return "no target"


def _action_to_phrase(a: dict) -> str:
    k = a.get("kind")
    if k == "cycle":
        off = a.get("power_off_seconds", 5)
        return f"power-cycle ({off}s off)"
    if k == "hold_off":
        return "hold off (power off until manually restored)"
    if k == "notify_only":
        return "notify (no power action)"
    return "no action"


def _seconds_to_phrase(s: int) -> str:
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    return f"{s // 3600} h {(s % 3600) // 60} min".rstrip()
