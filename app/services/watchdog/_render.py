"""Plain-English rule renderer (R-WD-1).

Pure presentation — no `session_scope()`, no model writes. Consumes
`WatchdogRule` and dict-shaped probe/target/action and produces the
operator-facing sentence rendered on the rules list page.

Split from the legacy single-file `services/watchdog.py` in v0.6.48
under the `services/<x>/` subpackage convention; see architecture.md
§"Service subpackages". External callers MUST import via
`app.services.watchdog`, never this module directly.
"""

from __future__ import annotations

from app.models import WatchdogRule
from app.models.watchdog import ACTION_KIND_BINDING


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
    if k == "device_heartbeat_stale":
        return (
            f"device `{p.get('device_id','?')}` has not heartbeated in "
            f"{p.get('max_age_seconds', 300)} s (gone / silent)"
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
    if k == "apply_scene":
        if a.get("scene_id"):
            return "apply a saved scene"
        n = len(a.get("items") or [])
        return f"apply a {n}-device scene"
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
