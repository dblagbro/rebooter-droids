"""Read-only watchdog queries + serializer.

Split from the legacy single-file `services/watchdog.py` in v0.6.48.
Every function here opens its own `session_scope()` and returns
plain dicts — no ORM rows leak past the public surface.

External callers MUST import via `app.services.watchdog`, never this
module directly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import Device, Group, WatchdogRule

from app.services.watchdog._render import render_rule_sentence


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
        # v0.4.14 (BUG-042): expose v0.4.2 runtime state. Templates
        # already referenced these fields; before this fix they
        # rendered as empty strings.
        "failure_streak": getattr(r, "failure_streak", 0) or 0,
        "recovery_streak": getattr(r, "recovery_streak", 0) or 0,
        "last_probed_at": _iso(getattr(r, "last_probed_at", None)),
        "last_action_at": _iso(getattr(r, "last_action_at", None)),
        "last_outcome": getattr(r, "last_outcome", None),
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


def list_rules_for_device(device_id: str) -> list[dict]:
    """Watchdog rules whose target resolves to include `device_id`.

    Reuses the runtime's `resolve_target_devices` so the device-detail
    page shows exactly what the watchdog would act on — direct `device`
    targets and `group` memberships. (`tag` targets resolve to no
    devices today, so a tag-targeted rule appears on no device's page —
    consistent with the runtime treating them as a no-op.) Distinct
    targets are resolved once each."""
    from app.services.watchdog_runtime import resolve_target_devices

    resolved: dict[str, list[str]] = {}
    out: list[dict] = []
    for rule in list_rules():
        target = rule.get("target") or {}
        key = json.dumps(target, sort_keys=True)
        if key not in resolved:
            resolved[key] = resolve_target_devices(target)
        if device_id in resolved[key]:
            out.append(rule)
    return out


def list_recent_events(rule_id: str, limit: int = 50) -> list[dict]:
    """v0.4.2: latest probe events for a rule, newest first."""
    from app.models import WatchdogProbeEvent

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(WatchdogProbeEvent)
                .where(WatchdogProbeEvent.rule_id == rule_id)
                .order_by(WatchdogProbeEvent.at.desc())
                .limit(limit)
            )
        )
        return [
            {
                "id": r.id,
                "at": _iso(r.at),
                "outcome": r.outcome,
                "details": r.details or {},
            }
            for r in rows
        ]


def probe_now(rule_id: str) -> dict | None:
    """v0.4.2: synchronously run a single probe for the rule, log it,
    and return the resulting event (does NOT advance state machine
    or fire actions — operator-facing diagnostic only)."""
    from app.services.watchdog_runtime import record_event, run_probe

    with session_scope() as session:
        rule = session.get(WatchdogRule, rule_id)
        if rule is None:
            return None
        try:
            outcome, details = run_probe(rule)
        except Exception as e:
            outcome, details = "probe_error", {"error": str(e)}
        details = {**(details or {}), "via": "probe_now"}
        now = datetime.now(timezone.utc)
        record_event(session, rule, outcome, details, now)
        session.flush()
        return {"outcome": outcome, "details": details, "at": _iso(now)}
