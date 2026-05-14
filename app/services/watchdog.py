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
    # v0.4.11 (BUG-036): cap to column width before insert.
    if len(name) > 120:
        raise WatchdogValidationError("name must be 120 characters or fewer")
    if not isinstance(probe, dict) or probe.get("kind") not in KNOWN_PROBE_KINDS:
        raise WatchdogValidationError(
            f"probe.kind must be one of {KNOWN_PROBE_KINDS}"
        )
    # v0.5.9: when probe.kind=='internet' the rule may pin an explicit
    # outbound-target list. Validate shape so a malformed entry can't
    # get persisted and silently degrade the runtime to "no targets ⇒
    # always failure" on the next tick.
    if probe.get("kind") == "internet" and "targets" in probe:
        targets = probe.get("targets")
        if targets is not None:
            if not isinstance(targets, list):
                raise WatchdogValidationError(
                    "probe.targets must be a list of {host, port} objects"
                )
            if len(targets) > 8:
                raise WatchdogValidationError(
                    "probe.targets accepts at most 8 entries"
                )
            for i, t in enumerate(targets):
                if not isinstance(t, dict):
                    raise WatchdogValidationError(
                        f"probe.targets[{i}] must be an object with host + port"
                    )
                host = str(t.get("host") or "").strip()
                if not host:
                    raise WatchdogValidationError(
                        f"probe.targets[{i}].host is required"
                    )
                try:
                    port = int(t.get("port") or 0)
                except (TypeError, ValueError):
                    raise WatchdogValidationError(
                        f"probe.targets[{i}].port must be an integer"
                    )
                if port < 1 or port > 65535:
                    raise WatchdogValidationError(
                        f"probe.targets[{i}].port must be between 1 and 65535"
                    )
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
    if not isinstance(action, dict) or action.get("kind") not in (
        "cycle", "hold_off", "notify_only"
    ):
        raise WatchdogValidationError(
            "action.kind must be 'cycle' | 'hold_off' | 'notify_only'"
        )

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
        targets = p.get("targets") or []
        if isinstance(targets, list) and targets:
            return f"outbound internet connectivity ({len(targets)} target{'s' if len(targets) != 1 else ''})"
        return "outbound internet connectivity (3 default targets)"
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
