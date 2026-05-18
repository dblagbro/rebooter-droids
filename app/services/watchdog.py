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
    ACTION_KIND_BINDING,
    KNOWN_ACTION_KINDS,
    KNOWN_PROBE_KINDS,
    LEAF_ACTION_KINDS,
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


# v0.5.34 (BUG-055 fix): per-kind probe-field validation. Called from
# both `create_rule` and `update_rule` so the JSON-editor + API paths
# get the same gate the rules-create form UI already enforces via
# HTML5 `required`/`min`/`max`. Pattern mirrors
# `services.external_sensors._validate_kind_config()`.
#
# Raises `WatchdogValidationError` with an operator-friendly message
# on bad shape. Returns silently on success.
#
# Internet's `targets` list validation (v0.5.9) is folded in here so
# the previously-duplicated block in create_rule + update_rule
# collapses to a single source of truth.

_WEATHER_SEVERITIES = ("Minor", "Moderate", "Severe", "Extreme")


def _validate_probe(probe: dict) -> None:
    """Per-kind probe-field validation. The kind-presence + kind-in-
    canonical check still lives at the call site (because the error
    message format there carries the full KNOWN_PROBE_KINDS tuple).
    This helper handles everything *after* "yes, the kind is canonical"."""
    if not isinstance(probe, dict):
        raise WatchdogValidationError("probe must be a JSON object")
    kind = probe.get("kind")

    def _require(field: str, kind_name: str | None = None):
        val = probe.get(field)
        if val is None or (isinstance(val, str) and not val.strip()):
            label = kind_name or kind
            raise WatchdogValidationError(
                f"probe.{field} is required when probe.kind={label!r}"
            )

    def _require_numeric(field: str, *, low: float, high: float):
        raw = probe.get(field)
        if raw is None:
            raise WatchdogValidationError(
                f"probe.{field} is required when probe.kind={kind!r}"
            )
        try:
            v = float(raw)
        except (TypeError, ValueError):
            raise WatchdogValidationError(
                f"probe.{field} must be numeric (got {type(raw).__name__}: {raw!r})"
            ) from None
        if v < low or v > high:
            raise WatchdogValidationError(
                f"probe.{field} must be between {low} and {high} (got {v})"
            )
        return v

    def _require_int(field: str, *, low: int, high: int):
        raw = probe.get(field)
        if raw is None:
            raise WatchdogValidationError(
                f"probe.{field} is required when probe.kind={kind!r}"
            )
        try:
            v = int(raw)
        except (TypeError, ValueError):
            raise WatchdogValidationError(
                f"probe.{field} must be an integer (got {type(raw).__name__}: {raw!r})"
            ) from None
        if v < low or v > high:
            raise WatchdogValidationError(
                f"probe.{field} must be between {low} and {high} (got {v})"
            )
        return v

    if kind == "internet":
        # v0.5.9: optional `targets` list. Empty/absent falls back to
        # DEFAULT_INTERNET_TARGETS in the runtime; the validator only
        # rejects bad shapes when the field IS present.
        if "targets" in probe and probe.get("targets") is not None:
            targets = probe["targets"]
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
        return

    if kind == "ping":
        _require("host")
        return

    if kind == "tcp":
        _require("host")
        _require_int("port", low=1, high=65535)
        return

    if kind == "http":
        url = (probe.get("url") or "").strip()
        if not url:
            raise WatchdogValidationError(
                "probe.url is required when probe.kind='http'"
            )
        if not (url.startswith("http://") or url.startswith("https://")):
            raise WatchdogValidationError(
                "probe.url must use http:// or https:// scheme"
            )
        return

    if kind == "dns":
        _require("hostname")
        return

    if kind == "gateway":
        # Per the runtime comment: device-side gateway IP wiring is the
        # missing piece — no per-rule fields to validate today.
        return

    if kind == "roku_app_active":
        _require("source_id")
        _require("app_name")
        return

    if kind == "ha_state_is":
        _require("source_id")
        _require("entity_id")
        _require("expected_state")
        return

    if kind == "weather_alert_active":
        _require("source_id")
        sev = (probe.get("min_severity") or "").strip()
        if sev and sev not in _WEATHER_SEVERITIES:
            raise WatchdogValidationError(
                f"probe.min_severity must be one of {_WEATHER_SEVERITIES} (got {sev!r})"
            )
        return

    if kind == "ical_event_active":
        _require("source_id")
        return

    if kind in ("power_above", "power_below"):
        _require("device_id")
        _require_numeric("threshold_w", low=0, high=10000)
        # window_seconds is optional; default 300 used by runtime.
        if "window_seconds" in probe:
            _require_int("window_seconds", low=30, high=86400)
        return

    if kind == "power_zero_while_on":
        _require("device_id")
        if "near_zero_threshold_w" in probe:
            _require_numeric("near_zero_threshold_w", low=0, high=100)
        if "window_seconds" in probe:
            _require_int("window_seconds", low=30, high=86400)
        return

    # v0.5.89 (BUG-058): the remaining runtime-supported integration
    # probe kinds. Required fields mirror what each `_probe_*` handler
    # in watchdog_runtime/_probes_integrations.py reads.
    if kind in ("ha_numeric_above", "ha_numeric_below"):
        _require("source_id")
        _require("entity_id")
        # HA numeric attributes span temperatures, percentages, watts —
        # the value is genuinely unbounded, so the range is only a
        # sanity cap against a fat-fingered exponent.
        _require_numeric("threshold", low=-1_000_000, high=1_000_000)
        return

    if kind in ("solar_production_above", "solar_production_below"):
        _require("source_id")
        _require_numeric("threshold_w", low=0, high=1_000_000)
        return

    if kind == "snmp_interface_down":
        _require("source_id")
        _require("interface")
        return

    if kind in ("snmp_throughput_above", "snmp_throughput_below"):
        _require("source_id")
        _require("interface")
        _require_numeric("threshold_bps", low=0, high=1_000_000_000_000)
        return

    if kind == "snmp_error_rate_above":
        _require("source_id")
        _require("interface")
        _require_numeric("threshold_errors_per_min", low=0, high=1_000_000_000)
        return

    if kind == "media_session_active":
        _require("source_id")
        return

    if kind == "webhook_field_equals":
        _require("source_id")
        _require("field")
        # `expected` is optional — the runtime defaults a missing value
        # to "" and an empty-string comparison is still a valid rule.
        return

    if kind == "mqtt_topic_equals":
        _require("source_id")
        _require("topic")
        # `expected_value` optional — same rationale as webhook above.
        return

    if kind == "epg_show_airing":
        # EPG reads the shared TVMaze cache, not a per-source row, so
        # `show` is the only required field; `network` is an optional
        # disambiguator.
        _require("show")
        return

    if kind == "host_awake":
        # TCP-connect alias — `host` required, `port` defaults to 22.
        _require("host")
        if "port" in probe:
            _require_int("port", low=1, high=65535)
        return

    # Unknown but kind-was-in-canonical (defensive — shouldn't reach
    # here because create_rule's KNOWN_PROBE_KINDS gate fires first).
    # Future kinds that get added to KNOWN_PROBE_KINDS without a
    # branch here will land in this fallback.
    raise WatchdogValidationError(
        f"probe.kind={kind!r} is canonical but has no validator — "
        f"add a branch in services.watchdog._validate_probe()"
    )


def _validate_action(action: dict, *, field: str = "action") -> None:
    """v0.5.90 (Stage A): validate a rule action.

    A *leaf* action is one of `LEAF_ACTION_KINDS`. A `binding` action
    is level-triggered — it carries `on_active` + `on_clear`, each
    itself a leaf action; the runtime applies them as the probe state
    flips (see `watchdog_runtime/_state.py::_binding_tick`). Binding
    actions never nest.
    """
    if not isinstance(action, dict):
        raise WatchdogValidationError(f"{field} must be a JSON object")
    kind = action.get("kind")
    if kind == ACTION_KIND_BINDING:
        for sub in ("on_active", "on_clear"):
            sub_action = action.get(sub)
            if not isinstance(sub_action, dict):
                raise WatchdogValidationError(
                    f"{field}.{sub} is required for a binding action and "
                    f"must be a JSON object"
                )
            if sub_action.get("kind") not in LEAF_ACTION_KINDS:
                raise WatchdogValidationError(
                    f"{field}.{sub}.kind must be one of {LEAF_ACTION_KINDS}"
                )
        return
    if kind not in LEAF_ACTION_KINDS:
        raise WatchdogValidationError(
            f"{field}.kind must be one of {KNOWN_ACTION_KINDS}"
        )


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
    # v0.5.34 (BUG-055 fix): per-kind probe-field validation. Replaces
    # the v0.5.9 internet-only inline validator with a full dispatcher
    # covering all 13 canonical kinds. Same helper called from
    # update_rule below for symmetry.
    _validate_probe(probe)
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
    _validate_action(action)

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
    # Reuse create_rule's validation block by raising the same errors.
    # Pre-validate before opening the session so we don't half-mutate.
    name = (name or "").strip()
    if not name:
        raise WatchdogValidationError("name is required")
    if len(name) > 120:
        raise WatchdogValidationError("name must be 120 characters or fewer")
    if not isinstance(probe, dict) or probe.get("kind") not in KNOWN_PROBE_KINDS:
        raise WatchdogValidationError(
            f"probe.kind must be one of {KNOWN_PROBE_KINDS}"
        )
    # v0.5.34 (BUG-055 fix): same per-kind validator as create_rule.
    _validate_probe(probe)
    if not isinstance(target, dict) or target.get("kind") not in (
        "device", "group", "tag"
    ):
        raise WatchdogValidationError(
            "target.kind must be 'device' | 'group' | 'tag'"
        )
    if target["kind"] in ("device", "group") and not (target.get("id") or "").strip():
        raise WatchdogValidationError(
            f"target.id is required when target.kind={target['kind']!r}"
        )
    if target["kind"] == "tag" and not (target.get("tag") or "").strip():
        raise WatchdogValidationError("target.tag is required when target.kind='tag'")
    _validate_action(action)
    if int(failure_threshold) < 1 or int(failure_threshold) > 100:
        raise WatchdogValidationError("failure_threshold must be between 1 and 100")
    if int(recovery_threshold) < 1 or int(recovery_threshold) > 100:
        raise WatchdogValidationError("recovery_threshold must be between 1 and 100")
    if int(window_seconds) < 5 or int(window_seconds) > 86400:
        raise WatchdogValidationError("window_seconds must be between 5 and 86400 (1 day)")
    if int(cooldown_seconds) < 0 or int(cooldown_seconds) > 86400:
        raise WatchdogValidationError("cooldown_seconds must be between 0 and 86400 (1 day)")

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

    # v0.5.90 (Stage A): a binding rule reads as a level-triggered
    # "state follows the condition" sentence, not the failure-streak
    # remediation shape.
    if action.get("kind") == ACTION_KIND_BINDING:
        on = _action_to_phrase(action.get("on_active") or {})
        off = _action_to_phrase(action.get("on_clear") or {})
        return (
            f"While {probe_str}, {on} on {target_str}; "
            f"when it clears, {off}."
        )

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
    # v0.5.34 (BUG-054 fix): `custom` branch removed — the kind is no
    # longer in KNOWN_PROBE_KINDS, so an old DB row carrying
    # `probe.kind='custom'` falls through to the "unknown probe"
    # generic phrase below (and the runtime returns failure with
    # reason='unknown probe kind: custom' which surfaces it clearly).
    # v0.5.25 (Phase 2A): external-source probes — render with the
    # source-id and the per-kind match field so the rules-list plain-
    # English sentence is informative even before Phase 2B's form
    # fields ship.
    if k == "roku_app_active":
        return f"Roku source `{p.get('source_id','?')}` showing app matching `{p.get('app_name','?')}`"
    if k == "ha_state_is":
        return (
            f"Home Assistant source `{p.get('source_id','?')}` entity "
            f"`{p.get('entity_id','?')}` in state `{p.get('expected_state','?')}`"
        )
    if k == "weather_alert_active":
        ev = p.get("event_contains")
        sev = p.get("min_severity")
        bits: list[str] = []
        if ev:
            bits.append(f"event contains `{ev}`")
        if sev:
            bits.append(f"severity ≥ `{sev}`")
        suffix = f" matching ({', '.join(bits)})" if bits else " (any active)"
        return f"weather source `{p.get('source_id','?')}` has alerts{suffix}"
    if k == "ical_event_active":
        summary = p.get("summary_contains")
        if summary:
            return (
                f"calendar source `{p.get('source_id','?')}` has event "
                f"matching `{summary}` currently airing"
            )
        return f"calendar source `{p.get('source_id','?')}` has any event currently airing"
    # v0.5.32 (B16 Phase 1D): power-targeted probes.
    if k == "power_above":
        return (
            f"device `{p.get('device_id','?')}` averaging > {p.get('threshold_w','?')} W "
            f"over {p.get('window_seconds', 300)} s"
        )
    if k == "power_below":
        return (
            f"device `{p.get('device_id','?')}` averaging < {p.get('threshold_w','?')} W "
            f"over {p.get('window_seconds', 300)} s"
        )
    if k == "power_zero_while_on":
        return (
            f"device `{p.get('device_id','?')}` drawing near-zero "
            f"(< {p.get('near_zero_threshold_w', 0.5)} W) while relay is on"
        )
    # v0.5.89 (BUG-058): the remaining canonical integration probes.
    if k in ("ha_numeric_above", "ha_numeric_below"):
        op = ">" if k == "ha_numeric_above" else "<"
        attr = p.get("attribute")
        what = f"`{p.get('entity_id','?')}`" + (f" attribute `{attr}`" if attr else "")
        return (
            f"Home Assistant source `{p.get('source_id','?')}` entity "
            f"{what} {op} {p.get('threshold','?')}"
        )
    if k in ("solar_production_above", "solar_production_below"):
        op = ">" if k == "solar_production_above" else "<"
        return (
            f"solar source `{p.get('source_id','?')}` producing "
            f"{op} {p.get('threshold_w','?')} W"
        )
    if k == "snmp_interface_down":
        return (
            f"SNMP source `{p.get('source_id','?')}` interface "
            f"`{p.get('interface','?')}` is down"
        )
    if k in ("snmp_throughput_above", "snmp_throughput_below"):
        op = ">" if k == "snmp_throughput_above" else "<"
        return (
            f"SNMP source `{p.get('source_id','?')}` interface "
            f"`{p.get('interface','?')}` {p.get('direction','total')} throughput "
            f"{op} {p.get('threshold_bps','?')} bps"
        )
    if k == "snmp_error_rate_above":
        return (
            f"SNMP source `{p.get('source_id','?')}` interface "
            f"`{p.get('interface','?')}` error rate > "
            f"{p.get('threshold_errors_per_min','?')} errors/min"
        )
    if k == "media_session_active":
        return f"media source `{p.get('source_id','?')}` has an active session"
    if k == "webhook_field_equals":
        return (
            f"webhook source `{p.get('source_id','?')}` field "
            f"`{p.get('field','?')}` equals `{p.get('expected','')}`"
        )
    if k == "mqtt_topic_equals":
        return (
            f"MQTT source `{p.get('source_id','?')}` topic "
            f"`{p.get('topic','?')}` equals `{p.get('expected_value','')}`"
        )
    if k == "epg_show_airing":
        network = p.get("network")
        suffix = f" on `{network}`" if network else ""
        return f"EPG shows `{p.get('show','?')}` currently airing{suffix}"
    if k == "host_awake":
        return (
            f"TCP connect to `{p.get('host','?')}:{p.get('port', 22)}` "
            f"(host is awake)"
        )
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
    if k == "relay_on":
        return "turn power on"
    if k == "relay_off":
        return "turn power off"
    if k == "binding":
        on = _action_to_phrase(a.get("on_active") or {})
        off = _action_to_phrase(a.get("on_clear") or {})
        return f"{on} while the condition holds, {off} when it clears"
    return "no action"


def _seconds_to_phrase(s: int) -> str:
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    return f"{s // 3600} h {(s % 3600) // 60} min".rstrip()
