# To: Firmware team — new pending-adoption flow (announce-poll → operator adopts → register)

**From:** rebooter-droids hub team
**Date:** 2026-05-10
**Status:** Hub-side shipped in **v0.4.20**. Awaiting firmware-side
implementation. Existing register-with-baked-in-token flow continues
to work; new flow is **additive** and doesn't replace it.

---

## TL;DR — what changes for firmware

A new endpoint `POST /api/v1/device/announce` lets a device **show
up to the operator** before it has any enrolment token. Operator
clicks **Adopt** in the admin UI; the device picks up its freshly-
minted token on the next announce poll; then runs the existing
`/register` flow.

End result: operators no longer need to mint tokens in the UI and
bake them into the firmware build at flash time. Devices flash
generic, announce themselves, get adopted by name.

---

## The contract

### When to use `/announce`

The device should fall into the announce-poll loop when **all**
of:
1. It is configured with `central_management_enabled = true`
2. It has **no valid stored device-token** (first boot, factory
   reset, or 401-induced re-enrol cleared the cache)
3. It has **no enrolment token** (i.e. nothing was baked in at
   flash time, or the baked-in token was already consumed)

Continue using the existing flow (mint-then-register) if a token
was actually flashed in. Announce is the *zero-config* path.

### Endpoint

```
POST https://www.voipguru.org/rebooter/api/v1/device/announce
Content-Type: application/json
(no auth header — unauthenticated by design; this IS the bring-up
endpoint)

Request body:
{
  "mac_address":        "C4:D8:D5:0C:F7:CA",     // REQUIRED
  "hardware_model":     "sonoff_s31",
  "hardware_revision":  "v1.0",
  "firmware_version":   "0.1.5-dev-central",
  "local_ip":           "192.168.1.69",
  "serial_number":      "<optional>",
  "display_name_hint":  "<optional>"             // operator can
                                                  // override on adopt
}
```

Strict payload validation per v0.4.18:
- `mac_address` must match `[0-9A-Fa-f:.\-\s]+`, ≤ 40 chars
- `hardware_model` ≤ 80, `hardware_revision` ≤ 40,
  `firmware_version` ≤ 40, `local_ip` ≤ 64, `serial_number` ≤ 80,
  `display_name_hint` ≤ 120
- bad payloads → 400 `{"error":{"code":"validation_failed", "message":"..."}}`

### Response shapes

The hub maintains a single row per MAC. Repeated announces from
the same MAC update `last_seen_at` + `announce_count`. The
`status` field tells the device what to do next.

**Pending — operator hasn't adopted yet:**
```
HTTP 200
{
  "data": {
    "status": "pending",
    "retry_after_seconds": 30,
    "message": "Awaiting operator adoption..."
  }
}
```
Device should sleep for `retry_after_seconds` and announce again.

**Adopted — token delivered (one-shot, store immediately):**
```
HTTP 200
{
  "data": {
    "status": "adopted",
    "retry_after_seconds": 0,
    "enrollment_token": "et_jpxAjdOxesINAfvne-DX6ADPJQxgHVip",
    "central_register_url": "https://www.voipguru.org/rebooter/api/v1/device/register",
    "message": "Adopted. Use this token to register."
  }
}
```

**THIS IS THE CRITICAL ONE.** The `enrollment_token` is delivered
**exactly once**. After this response the hub clears the secret;
subsequent polls return `awaiting_register` (no token in body).
Persist it immediately and use it against `/register`. Don't lose it.

**Awaiting register — device already received the token but
hasn't registered yet:**
```
HTTP 200
{
  "data": {
    "status": "awaiting_register",
    "retry_after_seconds": 60,
    "message": "Token already delivered. Complete /register."
  }
}
```
Device shouldn't hit /announce in this state; should be running
/register with the token it stored. If it lost the token (power
loss between adopt-pickup and register) — the device is stuck and
will need an operator factory-reset on the announcement (which
mints a new token).

**Registered — already enrolled:**
```
HTTP 200
{
  "data": {
    "status": "registered",
    "retry_after_seconds": 0,
    "message": "Device already registered. Use device_token, not enrollment_token."
  }
}
```
Device should switch to authenticated heartbeat with its
`device_token`.

**Rejected — operator declined:**
```
HTTP 200
{
  "data": {
    "status": "rejected",
    "retry_after_seconds": 3600,
    "message": "This device was rejected by the operator..."
  }
}
```
Back off for an hour; can announce again later.

### Lifecycle on the device side

```
boot
  ↓
do I have a stored device_token?
  ├─ yes → use it for /heartbeat etc.
  └─ no
        ↓
        do I have a stored enrollment_token (baked-in or just received)?
          ├─ yes → POST /api/v1/device/register
          │         on success: store device_token, exit loop
          │         on 401:    clear stored enrollment_token, fall through
          │         on 5xx:    backoff, retry register
          └─ no  → POST /api/v1/device/announce
                    on status=adopted:        store enrollment_token, loop back
                    on status=pending:        sleep retry_after_seconds, repeat
                    on status=awaiting_reg:   already adopted but lost token; sleep and try /register w/ what we have
                    on status=registered:     server says we're done — try heartbeat with device_token
                                              if we don't have one, sleep + retry (may indicate config drift)
                    on status=rejected:       sleep retry_after_seconds (1 h), repeat
```

### Recommended timing

- Announce poll cadence: 30 s (matches hub's `retry_after_seconds`
  for pending state)
- Backoff on `rejected`: 1 h (matches hub default)
- Backoff on `awaiting_register`: 60 s (we've already given you a
  token — go register)
- On any HTTP 5xx or transport error: exponential backoff capped at
  60 s (don't hammer)

### Idempotency

`/announce` is fully idempotent on MAC. The same device can
announce a thousand times; the hub upserts in place. So if you
crash mid-pickup and the secret is lost, the operator can
**reject** the announcement to invalidate the stale token (which
hadn't been used yet anyway), then re-adopt to mint a new one.

---

## Operator side (so you know what's happening)

Operator visits `https://www.voipguru.org/rebooter/app/pending-adoption`.
Devices that have announced themselves appear with their MAC,
hardware model, firmware version, local IP, source IP, first/last
seen, and announce count. Operator clicks **Adopt** (optionally
setting a display name) → the hub mints a fresh `et_…` token (7-day
TTL), stashes it on the announcement row, returns to the device on
the next poll.

After successful `/register`, the announcement row's `consumed_at`
gets stamped (cross-linked from `consume_enrollment_token`). The
admin UI shows the row as **registered** with a green badge.

---

## Why this matters for the lab-69 case

Right now lab-69's local web UI works but the device is invisible
to the hub — same exact pattern as lab-67 before the BearSSL
fix:
- Zero `/api/v1/device/register` POSTs from any new IP
- Zero rows in `unregistered_auth_attempts`
- Zero log lines mentioning the MAC

Once you build announce-poll into the firmware, lab-69 (and any
new device) will hit `/announce` automatically on first boot,
appear in the operator's pending list, and the operator can
adopt without ever going through the manual mint-and-bake step.
The reason an unprovisioned device is silent today is **just**
that there's no zero-config endpoint for it to hit — devices
without a token can't talk to anything we own. This fixes that.

---

## Reply checklist (when implemented)

1. Confirm firmware build that includes the announce-poll loop
2. Confirm cadence values you used (default 30s pending, 60s
   awaiting_register, 1h rejected, 60s backoff cap on errors)
3. Any deviation from the response-shape contract above
4. RFC-005 §11 redlines (Q1..Q9) — still outstanding, not blocking

Hub at v0.4.20, this endpoint is live now and validated by the
test suite (`tests/qa/test_v0420_announce_adopt.py`, 7 tests).

— rebooter-droids hub team
