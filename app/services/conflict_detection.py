"""Deterministic watchdog-rule conflict detection — v1.x.

`watchdog.create_rule()` validation is *single-rule, structural*: it
checks one rule is well-formed (probe kind known, required fields
present, numeric bounds). It has **no cross-rule semantic awareness** —
today you can create two rules that fight each other and `create_rule()`
accepts both.

This module adds a **cross-rule, cross-schedule, semantic** layer *on
top of* that structural validation. It does not replace it.

`detect_conflicts()` is a standalone, deterministic service — no AI, no
model call, no network. It is pulled forward from the v2 AI-layer design
(`rebooter-v2-ai-layer-design-2026-05-20.md` §4) so the regular,
non-AI Rules form benefits from the same checks today and so the future
v2 AI orchestrator can reuse this function unchanged.

Tenant boundary: every query in this module runs through the ordinary
org-scoped service layer (`watchdog.list_rules`, `schedules.list_all`),
so it only ever evaluates rules and schedules inside the acting user's
active org. There is no `tenant_scope.system()` bypass anywhere here.

Severity ladder (v2 design §4.2):
  - ``block`` — the dangerous case. Mains power would oscillate, stick,
    or fight itself. The Rules form requires an explicit operator
    acknowledgment before such a rule can be saved.
  - ``warn``  — heuristic concern. Shown to the operator; save allowed.
  - ``info``  — informational (likely-redundant rule). Save allowed.

Because these are heuristics, a ``block`` finding is *not* an
unbypassable hard block — it is a confirm step (see
`app/blueprints/admin/rules.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.watchdog import ACTION_KIND_BINDING

# Severity levels.
SEVERITY_BLOCK = "block"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"

# Conflict kinds — stable identifiers so the UI / tests / future v2 AI
# layer can branch on them without string-matching the message.
KIND_CONTRADICTORY_ACTIONS = "contradictory_actions"
KIND_WATCHDOG_VS_SCHEDULE = "watchdog_vs_schedule"
KIND_OVERLAPPING_SCHEDULES = "overlapping_schedules"
KIND_DUPLICATE_RULE = "duplicate_rule"
KIND_POWER_CYCLE_LOOP = "power_cycle_loop"
KIND_MODE_RULE_MISMATCH = "mode_rule_mismatch"


@dataclass
class ConflictFinding:
    """One detected conflict.

    `severity` is one of the SEVERITY_* constants; `kind` one of the
    KIND_* constants; `message` is operator-facing plain language;
    `related_ids` carries the ids of the other rules / schedules /
    devices involved so the UI can link to them.
    """

    severity: str
    kind: str
    message: str
    related_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "kind": self.kind,
            "message": self.message,
            "related_ids": list(self.related_ids),
        }


# ── action-semantics helpers ────────────────────────────────────────────

# Leaf action kinds that energize / keep-on a relay.
_KEEP_ON_ACTIONS = frozenset({"relay_on"})
# Leaf action kinds that de-energize / keep-off a relay.
_KEEP_OFF_ACTIONS = frozenset({"hold_off", "relay_off"})
# Leaf action kinds that oscillate power (off-then-on).
_CYCLE_ACTIONS = frozenset({"cycle"})
# Actions that take no power action at all.
_PASSIVE_ACTIONS = frozenset({"notify_only"})


def _leaf_action_kinds(action: dict) -> set[str]:
    """Return the set of *leaf* action kinds an action resolves to.

    A plain leaf action resolves to itself. A ``binding`` resolves to
    both of its edges (`on_active` + `on_clear`) — both can drive the
    relay, so both count when reasoning about contradiction."""
    action = action or {}
    kind = action.get("kind")
    if kind == ACTION_KIND_BINDING:
        kinds: set[str] = set()
        for edge in ("on_active", "on_clear"):
            sub = action.get(edge) or {}
            sub_kind = sub.get("kind")
            if sub_kind:
                kinds.add(sub_kind)
        return kinds
    return {kind} if kind else set()


def _action_drives_power(action: dict) -> bool:
    """True when the action actually moves a relay (not notify-only,
    not a scene we cannot reason about device-by-device)."""
    kinds = _leaf_action_kinds(action)
    if not kinds:
        return False
    return bool(kinds & (_KEEP_ON_ACTIONS | _KEEP_OFF_ACTIONS | _CYCLE_ACTIONS))


def _power_intents(action: dict) -> set[str]:
    """Coarse power *intent* of an action: 'on', 'off' and/or 'cycle'.

    Used to decide whether two rules / a rule and a schedule fight. A
    binding contributes the intents of both its edges."""
    kinds = _leaf_action_kinds(action)
    intents: set[str] = set()
    if kinds & _KEEP_ON_ACTIONS:
        intents.add("on")
    if kinds & _KEEP_OFF_ACTIONS:
        intents.add("off")
    if kinds & _CYCLE_ACTIONS:
        intents.add("cycle")
    return intents


def _probe_signature(probe: dict) -> tuple:
    """A hashable, comparison-stable signature of a probe — used to
    decide whether two rules probe the *same thing*. Only the
    identity-bearing fields per kind are included (not tuning fields
    like ``max_sample_age_seconds``)."""
    probe = probe or {}
    kind = probe.get("kind")
    if kind == "internet":
        targets = probe.get("targets") or []
        norm = tuple(sorted(
            (str((t or {}).get("host", "")), int((t or {}).get("port", 0) or 0))
            for t in targets if isinstance(t, dict)
        ))
        return ("internet", norm)
    if kind == "ping":
        return ("ping", probe.get("host"))
    if kind == "tcp":
        return ("tcp", probe.get("host"), probe.get("port"))
    if kind == "http":
        return ("http", probe.get("url"))
    if kind == "dns":
        return ("dns", probe.get("hostname"))
    if kind == "gateway":
        return ("gateway",)
    if kind in ("power_above", "power_below", "power_zero_while_on"):
        return (kind, probe.get("device_id"), probe.get("threshold_w"),
                probe.get("near_zero_threshold_w"))
    # Integration probes — keyed by source + the per-kind match field.
    return (
        kind,
        probe.get("source_id"),
        probe.get("entity_id"),
        probe.get("app_name"),
        probe.get("topic"),
        probe.get("field"),
        probe.get("interface"),
        probe.get("show"),
    )


def _action_signature(action: dict) -> tuple:
    """A hashable signature of an action — for duplicate detection.
    Only the kind + power-shaping fields are compared."""
    action = action or {}
    kind = action.get("kind")
    if kind == "cycle":
        return ("cycle", action.get("power_off_seconds"))
    if kind == ACTION_KIND_BINDING:
        return (
            "binding",
            (action.get("on_active") or {}).get("kind"),
            (action.get("on_active") or {}).get("scene_id"),
            (action.get("on_clear") or {}).get("kind"),
            (action.get("on_clear") or {}).get("scene_id"),
        )
    if kind == "apply_scene":
        return ("apply_scene", action.get("scene_id"))
    return (kind,)


def _holdoff_seconds(action: dict) -> int | None:
    """The post-reboot hold-off a ``cycle`` action waits before it lets
    the device come back. None for non-cycle actions."""
    action = action or {}
    if action.get("kind") != "cycle":
        return None
    raw = action.get("post_reboot_holdoff_seconds")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# ── target resolution ───────────────────────────────────────────────────

def _resolve_devices(target: dict) -> set[str]:
    """Resolve a rule / schedule target to the set of device ids it
    acts on. Reuses the runtime resolver — the same one
    `watchdog.list_rules_for_device` uses — so detection sees exactly
    what the watchdog would act on."""
    from app.services.watchdog_runtime import resolve_target_devices

    try:
        return set(resolve_target_devices(target or {}))
    except Exception:
        return set()


# ── the engine ──────────────────────────────────────────────────────────

def detect_conflicts(
    proposed: dict,
    *,
    org_id: str | None = None,
    exclude_rule_id: str | None = None,
) -> list[ConflictFinding]:
    """Cross-rule / cross-schedule semantic conflict detection for one
    *proposed* watchdog rule.

    `proposed` is a rule-shaped dict — ``probe``, ``target``,
    ``action``, ``failure_threshold``, ``window_seconds``,
    ``cooldown_seconds`` (the shape `create_rule()` / `update_rule()`
    consume, or a serialized rule dict). It is the rule about to be
    created or edited.

    `org_id` is accepted for call-site symmetry with the v2 AI design
    (`detect_conflicts(proposed_change, *, org_id)`); the org boundary
    is *actually* enforced by the org-scoped service queries this
    function makes — `list_rules` / `list_all` only ever return the
    acting user's org's rows. It is never used to widen scope.

    `exclude_rule_id` — when editing an existing rule, pass its id so
    the rule is not flagged as conflicting with itself.

    Returns a list of `ConflictFinding`, most-severe first. Empty list
    means no semantic conflict was found.
    """
    from app.services import schedules as schedules_svc
    from app.services import watchdog as watchdog_svc

    findings: list[ConflictFinding] = []

    proposed_target = proposed.get("target") or {}
    proposed_action = proposed.get("action") or {}
    proposed_probe = proposed.get("probe") or {}
    proposed_devices = _resolve_devices(proposed_target)

    # Existing rules in this org, minus the rule being edited.
    existing_rules = [
        r for r in watchdog_svc.list_rules()
        if r.get("id") != exclude_rule_id
    ]
    existing_schedules = schedules_svc.list_all()

    # Pre-resolve every existing rule / schedule target once.
    rule_devices: dict[str, set[str]] = {}
    for r in existing_rules:
        rule_devices[r["id"]] = _resolve_devices(r.get("target") or {})
    sched_devices: dict[str, set[str]] = {}
    for s in existing_schedules:
        sched_devices[s["id"]] = _resolve_devices(s.get("target") or {})

    proposed_intents = _power_intents(proposed_action)
    proposed_drives = _action_drives_power(proposed_action)

    # ── Check 1 — contradictory actions on the same device ──────────────
    # A device targeted by the proposed rule with a keep-on / keep-off /
    # cycle intent, AND by another rule with the opposite keep intent.
    # Mains power would oscillate or stick. Severity: block.
    if proposed_drives and proposed_devices:
        for r in existing_rules:
            shared = proposed_devices & rule_devices.get(r["id"], set())
            if not shared:
                continue
            other_intents = _power_intents(r.get("action") or {})
            if not other_intents:
                continue
            contradiction = (
                ("on" in proposed_intents and "off" in other_intents)
                or ("off" in proposed_intents and "on" in other_intents)
            )
            if contradiction:
                findings.append(ConflictFinding(
                    severity=SEVERITY_BLOCK,
                    kind=KIND_CONTRADICTORY_ACTIONS,
                    message=(
                        f"This rule and rule \"{r['name']}\" drive opposite "
                        f"power states on the same device — one wants power "
                        f"ON, the other OFF. Mains power would stick or "
                        f"oscillate."
                    ),
                    related_ids=[r["id"], *sorted(shared)],
                ))

    # ── Check 2 — watchdog vs. schedule fighting ────────────────────────
    # A power_cycle schedule on a device the proposed rule also drives,
    # or a schedule whose effect opposes the rule's keep intent.
    if proposed_drives and proposed_devices:
        for s in existing_schedules:
            if s.get("kind") != "power_cycle":
                continue
            shared = proposed_devices & sched_devices.get(s["id"], set())
            if not shared:
                continue
            # A power_cycle schedule briefly drops power. A rule that
            # holds the device ON (relay_on / cycle-to-recover) is in
            # direct tension with a timed cycle; a hold_off rule plus a
            # cycle schedule is the classic fight (block).
            if "off" in proposed_intents:
                sev = SEVERITY_BLOCK
                detail = (
                    "the rule holds the device powered off while the "
                    "schedule power-cycles it on a timer — they fight."
                )
            else:
                sev = SEVERITY_WARN
                detail = (
                    "both act on the device's power — confirm the timed "
                    "cycle will not interrupt the watchdog's recovery."
                )
            findings.append(ConflictFinding(
                severity=sev,
                kind=KIND_WATCHDOG_VS_SCHEDULE,
                message=(
                    f"This rule and schedule \"{s['name']}\" both control "
                    f"power on the same device — {detail}"
                ),
                related_ids=[s["id"], *sorted(shared)],
            ))

    # ── Check 3 — overlapping schedules vs. the rule's recovery ─────────
    # Two power_cycle schedules at the same UTC time on a shared device
    # is itself an overlap; surfaced here when a rule is being created on
    # a device that already has multiple colliding power schedules.
    if proposed_devices:
        # Group schedules by (device, at_time_utc) and flag collisions.
        by_slot: dict[tuple[str, str], list[dict]] = {}
        for s in existing_schedules:
            if s.get("kind") != "power_cycle":
                continue
            at = s.get("at_time_utc") or s.get("start_at") or ""
            if not at:
                continue
            for dev in proposed_devices & sched_devices.get(s["id"], set()):
                by_slot.setdefault((dev, at), []).append(s)
        for (dev, at), slot in by_slot.items():
            if len(slot) > 1:
                findings.append(ConflictFinding(
                    severity=SEVERITY_WARN,
                    kind=KIND_OVERLAPPING_SCHEDULES,
                    message=(
                        f"The device this rule targets already has "
                        f"{len(slot)} power schedules firing at {at} — "
                        f"overlapping power actions at the same time."
                    ),
                    related_ids=[s["id"] for s in slot] + [dev],
                ))

    # ── Check 4 — duplicate / redundant rule ────────────────────────────
    # A proposed rule whose probe + target + action is functionally
    # identical to an existing one. Severity: info.
    proposed_sig = (
        _probe_signature(proposed_probe),
        _action_signature(proposed_action),
        proposed_target.get("kind"),
        proposed_target.get("id") or proposed_target.get("tag"),
    )
    for r in existing_rules:
        other_sig = (
            _probe_signature(r.get("probe") or {}),
            _action_signature(r.get("action") or {}),
            (r.get("target") or {}).get("kind"),
            (r.get("target") or {}).get("id")
            or (r.get("target") or {}).get("tag"),
        )
        if other_sig == proposed_sig:
            findings.append(ConflictFinding(
                severity=SEVERITY_INFO,
                kind=KIND_DUPLICATE_RULE,
                message=(
                    f"Rule \"{r['name']}\" already does the same thing — "
                    f"same probe, same target, same action. This rule is "
                    f"likely redundant."
                ),
                related_ids=[r["id"]],
            ))

    # ── Check 5 — cooldown-vs-holdoff power-cycle loop ──────────────────
    # Self-defeating timing: a cycle rule whose cooldown_seconds is
    # shorter than the post-reboot hold-off it itself waits, OR a
    # failure_threshold x window_seconds so short the device cannot
    # finish rebooting before the next probe fails again. Severity: warn.
    holdoff = _holdoff_seconds(proposed_action)
    cooldown = proposed.get("cooldown_seconds")
    try:
        cooldown_i = int(cooldown) if cooldown is not None else None
    except (TypeError, ValueError):
        cooldown_i = None
    if holdoff is not None and cooldown_i is not None and cooldown_i < holdoff:
        findings.append(ConflictFinding(
            severity=SEVERITY_WARN,
            kind=KIND_POWER_CYCLE_LOOP,
            message=(
                f"cooldown_seconds ({cooldown_i}s) is shorter than this "
                f"rule's post-reboot hold-off ({holdoff}s) — the rule can "
                f"re-fire before the device has finished coming back, "
                f"causing a power-cycle loop."
            ),
            related_ids=[],
        ))
    # Reboot-window footgun: a cycle action whose probe re-checks faster
    # than a reboot completes. window x failure_threshold is the time to
    # the next fire; if that is under the hold-off the device cannot
    # recover in time.
    if holdoff is not None:
        ft = proposed.get("failure_threshold")
        ws = proposed.get("window_seconds")
        try:
            ft_i = int(ft) if ft is not None else None
            ws_i = int(ws) if ws is not None else None
        except (TypeError, ValueError):
            ft_i = ws_i = None
        if ft_i and ws_i and (ft_i * ws_i) < holdoff:
            findings.append(ConflictFinding(
                severity=SEVERITY_WARN,
                kind=KIND_POWER_CYCLE_LOOP,
                message=(
                    f"failure_threshold x window_seconds "
                    f"({ft_i} x {ws_i} = {ft_i * ws_i}s) is shorter than the "
                    f"post-reboot hold-off ({holdoff}s) — the device cannot "
                    f"finish rebooting before the rule fires again."
                ),
                related_ids=[],
            ))

    # ── Check 6 — mode / rule mismatch ──────────────────────────────────
    # A device whose desired_mode is `smart_plug` (the setup wizard
    # explicitly generates NO watchdog rule for that mode) that this
    # proposal would attach a watchdog rule to. Severity: warn.
    #
    # `desired_mode` is read with an ordinary org-scoped ORM query — the
    # tenant read filter limits it to the acting org, so a device id from
    # another org simply resolves to nothing.
    if proposed_devices and proposed_drives:
        from app.db import session_scope
        from app.models import Device

        with session_scope() as session:
            for dev_id in sorted(proposed_devices):
                dev = session.get(Device, dev_id)
                if dev is None:
                    continue
                if dev.desired_mode == "smart_plug":
                    findings.append(ConflictFinding(
                        severity=SEVERITY_WARN,
                        kind=KIND_MODE_RULE_MISMATCH,
                        message=(
                            f"Device \"{dev.display_name or dev_id}\" is set "
                            f"to smart-plug mode, which is not meant to run "
                            f"watchdog rules. Confirm you meant to change the "
                            f"device's role."
                        ),
                        related_ids=[dev_id],
                    ))

    # Most-severe first so the UI shows blockers at the top.
    _order = {SEVERITY_BLOCK: 0, SEVERITY_WARN: 1, SEVERITY_INFO: 2}
    findings.sort(key=lambda f: _order.get(f.severity, 9))
    return findings


def has_blocking(findings: list[ConflictFinding]) -> bool:
    """True when any finding is `block`-severity — the Rules form uses
    this to decide whether to require an explicit acknowledgment."""
    return any(f.severity == SEVERITY_BLOCK for f in findings)
