"""Inbox / attention-feed for the v0.3.0+ Status page.

The Status page replaces the v0.2.x stat-grid dashboard. Its job is
to answer two questions on first glance:

  1. **Does anything need attention?**  (health verdict)
  2. **If yes, what and where?**         (attention items)

This module computes both. It is read-only. Best-effort: any
unexpected failure returns a safe `unknown` verdict + empty feed
rather than crashing the dashboard.

Health verdicts (R-DSH-2):
  - `all-clear`  — every non-fixture device online; no pending alerts
  - `attention`  — at least one device offline > N minutes OR a
                   recent enrollment without first heartbeat
  - `degraded`   — > 25% of non-fixture devices offline
  - `unknown`    — telemetry unreliable (DB query failed)

Attention item kinds (R-DSH-3):
  - `device_offline_short`   — heartbeated before, gone silent for >3 min
  - `device_offline_long`    — heartbeated before, gone silent > 24 h
  - `device_never`           — enrolled but never heartbeated > 30 min
  - `enrollment_pending`     — enrolled within last hour, no first heartbeat
  - `device_auth_rejected`   — device tried to call but bearer token was rejected
                               (v0.3.6: surfaces the unregistered_auth_attempts
                               signal so an operator can see "this device IS
                               trying but I need to re-enrol it" without
                               diving into /app/unregistered-devices)
  - `device_failsafe`        — device fell back from slot B → slot C after a
                               failed firmware update (v0.3.8 / RFC-005 P1).
                               Severity: critical — a failsafe means an
                               update we pushed didn't boot on a real device.
  - `firmware_update_seen`   — device booted on new firmware version

Each item carries a stable `id` (composite of kind + target) so a
future P2.5 acknowledge-action can dedupe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import Device

log = logging.getLogger(__name__)


# Tunable thresholds. Everything in seconds.
SHORT_OFFLINE_SECONDS = 3 * 60       # 3 min — beyond the heartbeat window
LONG_OFFLINE_SECONDS = 24 * 60 * 60  # 24 h
NEVER_HEARTBEATED_GRACE_SECONDS = 30 * 60   # ignore brand-new units < 30 min old
ENROLLMENT_PENDING_WINDOW_SECONDS = 60 * 60 # < 1 h since registration

# v0.3.6 device_auth_rejected:
# Only surface if the same (claimed_device_id, source_ip, endpoint)
# has been rejected at least 3 times in the lookback window —
# avoids noise from a single transient bad request.
DEVICE_AUTH_REJECTED_LOOKBACK_MINUTES = 60
DEVICE_AUTH_REJECTED_MIN_HITS = 3

# v0.3.8 device_failsafe:
# Look back 24 h. Any failsafe in this window surfaces — these are
# already strongly-shaped events from device firmware so we don't
# need a hit-count threshold to filter noise.
DEVICE_FAILSAFE_LOOKBACK_HOURS = 24

# v0.3.7: source IPs that are NEVER real devices. Filter them out
# of the attention feed so the operator doesn't see machine-
# internal noise (QA tests, healthchecks, container-to-container
# traffic that doesn't go through nginx + ProxyFix).
_NEVER_REAL_DEVICE_IPS = frozenset({
    "127.0.0.1",
    "::1",
    "192.168.18.1",  # docker bridge gateway as seen inside rebooter-droids container
})

# v0.3.7: claimed-device-id prefixes that mark a request as
# QA-test-shaped. Mirror the v0.2.8 fixture-prefix logic
# (services/enrollment.py::_QA_PREFIXES) plus the dev_QA_ shape
# the v0.3.6 attention-feed test happens to emit.
_QA_AUTH_REJECTED_PREFIXES = (
    "qa ", "qa-", "qa_",
    "test-", "test_",
    "playwright",
    "dev_qa_",  # the v0.3.6 test bucket's synthetic device-id shape
    "dev_test",
)


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def health_and_attention(limit: int = 50) -> dict:
    """Single entry point — returns the verdict + the ranked feed in
    one DB hit so the Status page renders consistently."""
    try:
        return _compute(limit=limit)
    except Exception:
        log.exception("inbox computation failed")
        return {
            "verdict": "unknown",
            "verdict_label": "Unknown",
            "verdict_message": "Could not read fleet telemetry. Try again.",
            "attention": [],
            "totals": {
                "attention_total": 0,
                "devices_total": 0,
                "devices_online": 0,
                "devices_offline_short": 0,
                "devices_offline_long": 0,
                "devices_never": 0,
                "enrollments_pending": 0,
                "auth_rejected": 0,
                "failsafe": 0,
            },
        }


def _compute(limit: int = 50) -> dict:
    now = datetime.now(timezone.utc)
    short_cutoff = now - timedelta(seconds=SHORT_OFFLINE_SECONDS)
    long_cutoff = now - timedelta(seconds=LONG_OFFLINE_SECONDS)
    never_grace = now - timedelta(seconds=NEVER_HEARTBEATED_GRACE_SECONDS)
    enroll_recent = now - timedelta(seconds=ENROLLMENT_PENDING_WINDOW_SECONDS)

    attention: list[dict] = []
    devices_total = 0
    devices_online = 0
    devices_offline_short = 0
    devices_offline_long = 0
    devices_never = 0
    enrollments_pending = 0

    with session_scope() as session:
        # Real fleet only — QA fixtures are excluded from health math
        # by design (R-DEV-2 + R-UX-13: never lie about state).
        rows = list(
            session.scalars(
                select(Device).where(Device.is_qa_fixture.is_(False))
            )
        )
        for d in rows:
            devices_total += 1
            hb = d.last_heartbeat_at

            if hb is None:
                # Never heartbeated. Bucket by age:
                if d.created_at >= enroll_recent:
                    enrollments_pending += 1
                    attention.append(
                        {
                            "kind": "enrollment_pending",
                            "id": f"enrollment_pending:{d.id}",
                            "severity": "info",
                            "title": "Newly enrolled — waiting for first heartbeat",
                            "device_id": d.id,
                            "device_name": d.display_name or d.id,
                            "since": _iso(d.created_at),
                            "rank": 30,
                        }
                    )
                elif d.created_at < never_grace:
                    devices_never += 1
                    attention.append(
                        {
                            "kind": "device_never",
                            "id": f"device_never:{d.id}",
                            "severity": "warn",
                            "title": "Never heartbeated since enrolment",
                            "device_id": d.id,
                            "device_name": d.display_name or d.id,
                            "since": _iso(d.created_at),
                            "hint": (
                                "Check the firmware: device powered? "
                                "Wi-Fi joined? `central_base_url` correct?"
                            ),
                            "rank": 50,
                        }
                    )
                # else: < 30 min old, no heartbeat — too early to alarm
            elif hb < long_cutoff:
                devices_offline_long += 1
                attention.append(
                    {
                        "kind": "device_offline_long",
                        "id": f"device_offline_long:{d.id}",
                        "severity": "warn",
                        "title": f"Offline > 24 h",
                        "device_id": d.id,
                        "device_name": d.display_name or d.id,
                        "since": _iso(hb),
                        "rank": 60,
                    }
                )
            elif hb < short_cutoff:
                devices_offline_short += 1
                attention.append(
                    {
                        "kind": "device_offline_short",
                        "id": f"device_offline_short:{d.id}",
                        "severity": "warn",
                        "title": "Offline > 3 min",
                        "device_id": d.id,
                        "device_name": d.display_name or d.id,
                        "since": _iso(hb),
                        "rank": 40,
                    }
                )
            else:
                devices_online += 1

            # v0.5.31 (Phase 4A): desired-config drift visibility.
            # Only fires for centrally-managed devices that have a
            # desired_config set. Two attention kinds:
            #   - desired_config_drifted — has reported_config AND
            #     fields differ (operator's intent isn't being applied).
            #   - desired_config_unconfirmed — has desired but no
            #     reported_config has ever arrived (device firmware
            #     may not echo apply_config yet, OR device hasn't
            #     received the apply yet).
            if (
                d.central_management_enabled
                and d.desired_config
                and isinstance(d.desired_config, dict)
                and d.desired_config  # non-empty
            ):
                reported = d.last_reported_config or {}
                if not isinstance(reported, dict) or not reported:
                    attention.append({
                        "kind": "desired_config_unconfirmed",
                        "id": f"desired_config_unconfirmed:{d.id}",
                        "severity": "info",
                        "title": "Desired config set but never reported back",
                        "device_id": d.id,
                        "device_name": d.display_name or d.id,
                        "since": _iso(d.desired_config_updated_at),
                        "hint": (
                            "Device hasn't echoed its config in a heartbeat "
                            "yet. Older firmware may not include "
                            "reported_config — check the firmware version."
                        ),
                        "rank": 35,
                    })
                else:
                    # Compute drift inline (cheap: small dicts).
                    missing: list[str] = []
                    mismatched: list[str] = []
                    for field, want in d.desired_config.items():
                        if field not in reported:
                            missing.append(field)
                        elif reported.get(field) != want:
                            mismatched.append(field)
                    if missing or mismatched:
                        bits: list[str] = []
                        if mismatched:
                            bits.append(
                                f"{len(mismatched)} mismatched"
                            )
                        if missing:
                            bits.append(f"{len(missing)} missing")
                        attention.append({
                            "kind": "desired_config_drifted",
                            "id": f"desired_config_drifted:{d.id}",
                            "severity": "warn",
                            "title": (
                                "Desired config drifted: " + ", ".join(bits)
                            ),
                            "device_id": d.id,
                            "device_name": d.display_name or d.id,
                            "since": _iso(d.desired_config_updated_at),
                            "hint": (
                                "Hub-side desired_config disagrees with the "
                                "device's last_reported_config. "
                                "Push from the device-detail Desired Config "
                                "card to reconcile."
                            ),
                            "rank": 70,
                        })

    # v0.3.6: device_auth_rejected items.
    # Surface unregistered_auth_attempts in the lookback window where the
    # same (device_id, source_ip, endpoint) tuple has been rejected at
    # least DEVICE_AUTH_REJECTED_MIN_HITS times — strong signal that a
    # real device is trying but holding a stale token.
    auth_rejected_count = 0
    try:
        from app.services import unregistered as unreg_service

        for row in unreg_service.list_recent(
            limit=50,
            since_minutes=DEVICE_AUTH_REJECTED_LOOKBACK_MINUTES,
        ):
            if row["hit_count"] < DEVICE_AUTH_REJECTED_MIN_HITS:
                continue
            ip = row["source_ip"] or "?"
            cdi = row["claimed_device_id"] or "(no device_id)"
            # v0.3.7: filter QA / machine-internal noise. The operator
            # cares about "is a real LAN device hitting central with a
            # bad token"; they do not care about our own QA tests
            # provoking 401s for regression coverage.
            if ip in _NEVER_REAL_DEVICE_IPS:
                continue
            cdi_lc = cdi.lower()
            if cdi_lc.startswith(_QA_AUTH_REJECTED_PREFIXES):
                continue
            auth_rejected_count += 1
            attention.append(
                {
                    "kind": "device_auth_rejected",
                    "id": f"device_auth_rejected:{cdi}:{ip}:{row['endpoint']}",
                    "severity": "warn",
                    "title": (
                        f"Device auth rejected ({row['hit_count']} attempts) "
                        f"on {row['endpoint']}"
                    ),
                    "device_id": cdi,
                    "device_name": cdi,
                    "source_ip": ip,
                    "since": row["last_seen_at"],
                    "hint": (
                        "A device is calling with a stale or unknown bearer "
                        "token. Mint a fresh enrollment token and re-enrol "
                        "the device — the firmware's 401 → re-enroll loop "
                        "should pick it up automatically."
                    ),
                    "rank": 35,
                }
            )
    except Exception:
        # Best-effort: never let a tracker query failure crash Status.
        log.exception("inbox.device_auth_rejected query failed")

    # v0.3.8: device_failsafe items.
    # Each row in device_failsafe_events from the last
    # DEVICE_FAILSAFE_LOOKBACK_HOURS hours becomes a critical-severity
    # attention item. A failsafe is a strong signal — an update we
    # pushed didn't boot on a real device.
    failsafe_count = 0
    try:
        from app.services import failsafe as failsafe_service

        # Look up display_name per device once for nicer titles.
        device_name_by_id: dict[str, str] = {}
        with session_scope() as fname_session:
            for d in fname_session.scalars(select(Device)):
                device_name_by_id[d.id] = d.display_name or d.id

        for fs in failsafe_service.list_recent(
            limit=20,
            since_hours=DEVICE_FAILSAFE_LOOKBACK_HOURS,
        ):
            failsafe_count += 1
            dev_id = fs["device_id"]
            dev_name = device_name_by_id.get(dev_id, dev_id)
            failed = fs.get("failed_version") or "?"
            fallback = fs.get("fallback_to_version") or "?"
            reason = fs.get("reason") or "?"
            attention.append(
                {
                    "kind": "device_failsafe",
                    "id": f"device_failsafe:{fs['id']}",
                    "severity": "critical",
                    "title": (
                        f"Firmware failsafe: {failed} → {fallback} "
                        f"(reason: {reason})"
                    ),
                    "device_id": dev_id,
                    "device_name": dev_name,
                    "since": fs["received_at"],
                    "hint": (
                        "The device fell back from a just-updated firmware "
                        "to its known-good previous version. Check the "
                        "device-detail page for the diagnostic blob and "
                        "consider rolling back the firmware deployment "
                        "until the failure is understood."
                    ),
                    "rank": 80,  # higher than offline_long=60
                }
            )
    except Exception:
        log.exception("inbox.device_failsafe query failed")

    # v0.4.7 (B13): watchdog.firing — surface rules that have either
    # status='firing' OR an action_fired event in the last hour. The
    # operator gets a one-click view of every rule currently demanding
    # attention.
    watchdog_firing_count = 0
    try:
        from app.models import WatchdogProbeEvent, WatchdogRule
        firing_cutoff = now - timedelta(hours=1)
        with session_scope() as session:
            rule_rows = list(session.scalars(
                select(WatchdogRule).where(
                    WatchdogRule.enabled.is_(True),
                )
            ))
            for r in rule_rows:
                # Two reasons we'd surface this rule:
                #   1. its status is firing (post-action), OR
                #   2. it logged an action_fired event in the last hour
                #      (caught even after the operator manually toggled
                #      it back to armed)
                fired_recently = session.scalar(
                    select(WatchdogProbeEvent.id)
                    .where(
                        WatchdogProbeEvent.rule_id == r.id,
                        WatchdogProbeEvent.outcome == "action_fired",
                        WatchdogProbeEvent.at >= firing_cutoff,
                    )
                    .limit(1)
                )
                if r.status != "firing" and fired_recently is None:
                    continue
                watchdog_firing_count += 1
                target = (r.target or {})
                target_label = (
                    f"device {target.get('id', '?')}"
                    if target.get("kind") == "device"
                    else f"group {target.get('id', '?')}"
                    if target.get("kind") == "group"
                    else f"tag `{target.get('tag', '?')}`"
                    if target.get("kind") == "tag"
                    else "?"
                )
                attention.append({
                    "kind": "watchdog_firing",
                    "id": f"watchdog_firing:{r.id}",
                    "severity": "warn",
                    "title": f"Watchdog rule firing: {r.name}",
                    # device_id/device_name are required by the
                    # template's existing item-row macro; reuse the
                    # rule_id as device_id since the click target
                    # routes to the rules page.
                    "device_id": r.id,
                    "device_name": r.name,
                    "since": _iso(r.last_action_at) if r.last_action_at else None,
                    "hint": (
                        f"Probe failing on {target_label}. "
                        f"Last outcome: {r.last_outcome or '?'}. "
                        f"See /app/rules for the event log."
                    ),
                    "rank": 70,  # between offline_long=60 and failsafe=80
                })
    except Exception:
        log.exception("inbox.watchdog_firing query failed")

    # v0.4.22 (Tier-2 E): hide items the operator has acked /
    # snoozed. Acks expire automatically when snooze_until passes.
    try:
        from app.services import attention_acks
        acked = attention_acks.acked_ids()
        if acked:
            attention = [a for a in attention if a.get("id") not in acked]
    except Exception:
        log.exception("inbox.attention_acks filter failed")

    attention.sort(key=lambda x: (-x["rank"], x.get("since") or ""), reverse=False)
    attention = attention[:limit]

    # Verdict logic (R-DSH-2 simplified for v0.3.1).
    attention_total = (
        devices_offline_short
        + devices_offline_long
        + devices_never
        + enrollments_pending
        + auth_rejected_count
        + failsafe_count
        + watchdog_firing_count
    )

    if devices_total == 0:
        verdict = "all-clear"
        verdict_label = "No devices yet"
        verdict_message = "Enrol your first device to start monitoring."
    elif devices_total > 0 and (
        devices_offline_short + devices_offline_long
    ) * 4 > devices_total:
        verdict = "degraded"
        verdict_label = "Degraded"
        verdict_message = (
            f"{devices_offline_short + devices_offline_long}"
            f" of {devices_total} devices offline"
        )
    elif attention_total > 0:
        verdict = "attention"
        verdict_label = "Attention"
        n = attention_total
        verdict_message = (
            f"{n} item{'s' if n != 1 else ''} need attention"
        )
    else:
        verdict = "all-clear"
        verdict_label = "All clear"
        verdict_message = (
            f"All {devices_total} device{'s' if devices_total != 1 else ''} online"
        )

    return {
        "verdict": verdict,
        "verdict_label": verdict_label,
        "verdict_message": verdict_message,
        # NOTE: this key MUST NOT be "items" — Jinja resolves
        # `obj.items` as the bound `dict.items` method, not a key.
        "attention": attention,
        "totals": {
            "attention_total": attention_total,
            "devices_total": devices_total,
            "devices_online": devices_online,
            "devices_offline_short": devices_offline_short,
            "devices_offline_long": devices_offline_long,
            "devices_never": devices_never,
            "enrollments_pending": enrollments_pending,
            "auth_rejected": auth_rejected_count,
            "failsafe": failsafe_count,
            "watchdog_firing": watchdog_firing_count,
        },
    }
