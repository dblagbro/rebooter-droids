# P2.2 / P2.3 — Router & Managed-Switch Telemetry — Research + Design

Date: 2026-05-15
Status: **design — research pass complete, not yet implemented**
Author: hub/backend
Track: P2 (zero-hardware-cost integrations) per
`docs/notes/2026-05-15-hub-team-status-sync-and-plan.md` §6
Prerequisite shipped: B17 external-sensor pattern (v0.5.17+), P2.1 solar (v0.5.56)

---

## 1. Purpose

P2.2 (router telemetry) and P2.3 (managed-switch telemetry) were flagged in
the hub-team plan as **research-gated** — unlike P2.1 solar, B17 had no
existing design for network telemetry (B17 Layer 2 EPG is TV-guide data, not
network). This note is that research+design pass.

**Target signals** (from the plan §6):
- Router: WAN up/down, throughput, error/retransmit counts, uptime.
- Switch: per-port link state, traffic, error counters.

Both are high-leverage covariates for the watchdog story (a wedged router is
exactly what Rebooter exists to power-cycle) and for power correlation (P3:
"the switch port went down *and* the plug load dropped").

---

## 2. Research findings

### 2.1 SNMP is the universal substrate

SNMP + the standard **IF-MIB** (RFC 2863) and **MIB-II** (RFC 1213) interface
table is supported by effectively every managed switch and prosumer router —
UniFi, MikroTik RouterOS, OpenWrt, pfSense/OPNsense, Netgear/TP-Link/Cisco
managed switches. It is **read-friendly telemetry by design**; configuration
is out of scope (that is TR-069/TR-369 / vendor APIs). P2.2/P2.3 are read-only
— SNMP fits exactly.

Standard OIDs the design relies on (all under `1.3.6.1.2.1`):

| Metric | OID name | Notes |
|---|---|---|
| Uptime | `sysUpTime` (`1.3.6.1.2.1.1.3.0`) | timeticks |
| Device name | `sysName` (`1.3.6.1.2.1.1.5.0`) | |
| Interface name | `ifName` / `ifDescr` | per-interface label |
| Link state | `ifOperStatus` | 1=up, 2=down, … |
| Link speed | `ifHighSpeed` | Mbit/s (use HC variant) |
| RX/TX bytes | `ifHCInOctets` / `ifHCOutOctets` | **64-bit — mandatory** |
| RX/TX errors | `ifInErrors` / `ifOutErrors` | monotonic counter |
| RX/TX discards | `ifInDiscards` / `ifOutDiscards` | monotonic counter |

**Counter-width caveat (critical):** 32-bit `ifInOctets` wraps in ~34 s on a
1 Gbps link. The design **must** use the 64-bit high-capacity counters
(`ifHCInOctets`, `ifHCOutOctets`) from the `ifXTable`. At 64-bit, wraparound
is a non-issue (years at 10 Gbps).

### 2.2 Vendor-API caveats — why SNMP first

- **UniFi**: supports SNMP, but USW Flex / Ultra switches do **not**, and
  UniFi's MIBs are not comprehensive for newer models. The UniFi *controller*
  API is richer but is a separate, auth-heavy integration.
- **MikroTik RouterOS**: solid SNMP (standard + vendor OIDs under
  `1.3.6.1.4.1.14988`); also a REST API that mirrors CLI.
- **OpenWrt**: SNMP via the `snmpd` package; richer data via `ubus`.

Industry direction (2026): SNMP remains the universal lowest-common-
denominator; shops layer REST/gRPC on top for richer context. For a
small/personal product, **SNMP alone covers the P2.2/P2.3 target signals**.
Vendor APIs are deferred to a later optional phase to fill SNMP gaps
(e.g. UniFi USW Flex).

### 2.3 Python SNMP tooling

| Option | Verdict |
|---|---|
| `pysnmp` | Full pure-Python engine, but the original author died (2022) and the project has since had fork/maintenance churn. Heavy. |
| `puresnmp` | Pure-Python, **zero dependencies**, deliberately small API. Viable. |
| `easysnmp` | net-snmp C bindings — fastest, but a compiled dependency. |
| **net-snmp CLI** (`snmpget`/`snmpbulkwalk`) | Shell out to the system binaries. |

**Decision: shell out to net-snmp CLI binaries.** Rationale:
1. **It mirrors the codebase precedent exactly** — `_probe_ping`
   (`app/services/watchdog_runtime/_probes.py`) already shells out to
   `/usr/bin/ping` with a `shutil.which` availability check and a fallback.
2. The external-sensors code deliberately avoids heavy deps — it hand-parses
   Roku XML and iCal rather than pull `lxml`/`icalendar`. Adding a SNMP engine
   dependency cuts against that ethos.
3. net-snmp is battle-tested, stable for decades, trivially available as the
   Debian `snmp` package.

`puresnmp` is the documented fallback if a future deployment target cannot
ship the net-snmp binaries. The poll driver should isolate the SNMP call
behind one function so swapping the backend is a one-file change.

**Dockerfile change required:** add `snmp` (net-snmp CLI) to the apt install
line — `snmpbulkwalk`, `snmpget`.

---

## 3. Architecture decision — one `kind='snmp'`

P2.1 shipped solar as two kinds (`solaredge`, `enphase_envoy`) sharing a probe
because the *transports* differed. For SNMP, **router and switch are the same
IF-MIB data** — the only difference is *which interface the operator cares
about* (a router's WAN port vs. a switch's access ports). That is a
**probe-level** distinction, not a poll-level one.

**Decision: a single `kind='snmp'`.** One poll driver walks the interface
table; the sample payload carries every interface; the watchdog probe selects
the interface by name. P2.2 and P2.3 are then the *same* integration — P2.3
costs nothing extra once P2.2's SNMP kind exists, which is exactly what the
plan anticipated ("can share an ingest pattern with it").

### 3.1 Schema fit — `external_sensor_sources`

Reuses the existing table unchanged (`config` JSONB already carries per-kind
extras):

```
kind   = "snmp"
host   = device IP / hostname        (needs_host = true)
port   = 161                          (default)
config = {
  "version":   "2c",                  # "2c" | "3"
  "community": "public",              # v2c — secret, redacted in admin API
  "v3": {                             # only when version == "3"
    "user": "...", "auth_proto": "SHA", "auth_key": "...",
    "priv_proto": "AES", "priv_key": "..."
  },
  "interface_filter": ["eth0", "wan"] # optional — limit stored interfaces
}
```

`community`, `v3.auth_key`, `v3.priv_key` join `_SECRET_CONFIG_KEYS` for
admin-API redaction (the P2.1 mechanism).

Default `poll_interval_seconds`: **120 s** (throughput needs ≥2 samples; 120 s
is a reasonable rate window and easy on the device).

### 3.2 Sample payload shape

```json
{
  "modality": "network",
  "sys_name": "office-router",
  "sys_uptime_seconds": 884213,
  "interfaces": {
    "wan": {
      "if_index": 2, "oper_status": "up", "speed_mbps": 1000,
      "in_octets": 184320551023, "out_octets": 42119008871,
      "in_errors": 0, "out_errors": 3,
      "in_discards": 12, "out_discards": 0
    },
    "lan1": { ... }
  }
}
```

`modality: "network"` tags the envelope for the P3 cross-modal query layer
(consistent with P1.1's `modality: "power"`). Octet/error fields are the raw
monotonic counters — the **probe** computes deltas (§5).

---

## 4. Poll driver

`_poll_snmp(host, port, config)` in `app/services/external_sensors.py`:

1. Build the net-snmp arg vector from `config` — v2c: `-v2c -c <community>`;
   v3: `-v3 -u <user> -l authPriv -a <auth_proto> -A <auth_key>
   -x <priv_proto> -X <priv_key>`.
2. One `snmpbulkwalk` of the `ifXTable` + `ifTable` columns listed in §2.1,
   plus two `snmpget`s for `sysName` / `sysUpTime`. `subprocess.run` with a
   hard timeout (~8 s), `capture_output=True` — same shape as `_probe_ping`.
3. Parse the `OID = TYPE: value` lines (net-snmp `-On`/`-Oqv` output is
   line-oriented and trivial to parse — no MIB files needed; pass numeric
   OIDs and `-Oqn`).
4. Join the per-column walks by `ifIndex` into the `interfaces` dict, keyed by
   `ifName`. Apply `interface_filter` if set.
5. Raise `RuntimeError` with a clear message on timeout / non-zero exit /
   auth failure (`snmpbulkwalk` prints `Timeout` / `Authentication failure`)
   so the existing `poll_source` error path records `last_error`.

`shutil.which("snmpbulkwalk")` guard at the top — if the binary is missing,
raise a clear "net-snmp not installed" error rather than a cryptic
`FileNotFoundError`.

---

## 5. The counter-delta mechanic (the one genuinely new piece)

Octets and errors are **monotonic counters**. Throughput and error-rate are
*rates* — they need two samples:

```
throughput_bps = (octets[t1] - octets[t0]) * 8 / (t1 - t0)
```

Solar and HA probes are point-in-time; SNMP rate probes are not. Design:

- The **poller stores raw counters** (no rate math at ingest — keeps ingestion
  dumb and idempotent).
- The **probe fetches the latest *two* samples** for the source and computes
  the delta. Add a helper to `external_sensors.py`:
  `last_two_samples(source_id, max_age_seconds) -> (newer, older) | None`.
- **Counter-reset guard:** if `newer < older` for a counter (device rebooted,
  counter reset), treat that interval as unavailable — return `success` with
  `reason="counter_reset"` rather than a wild negative rate. 64-bit HC
  counters do not wrap in practice, so a decrease means a reset.
- **Cold start:** the first poll has no predecessor — throughput/error-rate
  probes return `success` + `reason="insufficient_history"` (one sample).
  `snmp_interface_down` works off a single sample (point-in-time) and has no
  cold-start gap.

---

## 6. Watchdog probes

Three new kinds in `app/services/watchdog_runtime/_probes.py`, dispatched
like the P2.1 solar probes:

### `snmp_interface_down` — point-in-time
```json
{ "kind": "snmp_interface_down", "source_id": "ext_…",
  "interface": "wan", "max_sample_age_seconds": 600 }
```
Fails (→ builds toward the rule's action) when the interface's
`oper_status` != `up`. The **WAN-down detector** — pair with a `relay_cycle`
action on the modem's plug for "reboot the modem when the WAN link drops."

### `snmp_throughput_above` / `snmp_throughput_below` — rate
```json
{ "kind": "snmp_throughput_below", "source_id": "ext_…",
  "interface": "wan", "direction": "in",
  "threshold_bps": 1000000, "max_sample_age_seconds": 600 }
```
Computes bps from the last two samples (§5). `direction` ∈ `in`/`out`/`total`.
`snmp_throughput_below` is the "WAN is up but carrying no traffic — likely
wedged" signal that bare link-state misses.

### `snmp_error_rate_above` — rate
```json
{ "kind": "snmp_error_rate_above", "source_id": "ext_…",
  "interface": "lan3", "threshold_errors_per_min": 10,
  "max_sample_age_seconds": 600 }
```
Error-counter delta per minute vs. threshold — catches a flaky cable / dying
port (P2.3's per-port story).

All three: stale-sample gate (`max_sample_age_seconds`, default 600 s) and
the "failure = actionable condition" convention from `power_*` / `solar_*`.

---

## 7. UI

`templates/settings/integrations.html`: one add-source form (kind `snmp`) —
host, port (161), SNMP version select, community / v3 fields, optional
interface filter, poll interval (default 120). Per-kind sample summary row:
`<sys_name> · N interfaces · M up`. A rule-probe example block for the three
`snmp_*` kinds.

Optional follow-up (not required for the first ship): an interface browser
analogous to P2.4's HA entity browser — list the discovered interfaces so the
operator can copy an `interface` name into a rule.

---

## 8. Honest limitations / out of scope

- **Consumer ISP gateways** typically expose no SNMP. This integration targets
  **managed / prosumer** gear (UniFi, MikroTik, OpenWrt, pfSense/OPNsense,
  managed switches). An operator whose only router is a locked ISP box gets
  nothing here — accepted, and the UI copy should say so plainly.
- **UniFi USW Flex / Ultra** have no SNMP — a known gap; a future UniFi-
  controller-API kind would fill it.
- **No configuration / control** — read-only telemetry only. Power-cycling the
  device is the existing relay action; SNMP here never writes.
- **SNMPv2c community strings are plaintext on the wire.** Fine on a trusted
  LAN; v3 (auth+priv) is offered for operators who want it.

---

## 9. Effort estimate & phasing

| Item | Estimate |
|---|---|
| `kind='snmp'` + `_poll_snmp` + config validation + redaction | 3–4 h |
| `last_two_samples()` + counter-delta helper | 1 h |
| 3 watchdog probes (`snmp_interface_down` + 2 rate probes) | 2 h |
| Integrations UI form + sample summary | 1 h |
| Dockerfile `snmp` package + deploy verification | 0.5 h |
| **Total** | **~7–8 h — one version (≈v0.5.58)** |

P2.2 and P2.3 ship **together** in that one version — the single `kind='snmp'`
serves both; `snmp_interface_down` on a switch port *is* P2.3.

---

## 10. Open questions for the operator

1. **Does the operator actually run SNMP-capable gear?** This design is only
   worth building if there is a managed switch / UniFi / MikroTik / OpenWrt /
   pfSense box on site. If the only network device is an ISP gateway, P2.2/P2.3
   should be deferred indefinitely. **This is the gating question.**
2. **v2c only, or is v3 wanted?** v2c (community string) is simpler and fine on
   a trusted LAN; v3 adds auth+priv config surface. Recommend shipping v2c
   first, v3 behind the same config block if asked.
3. **Interface browser now or later?** The HA-style interface browser (P2.4
   precedent) is a nice discoverability aid but not required for the first
   ship — operators can read interface names off their device UI.

---

## 11. Recommendation

**Build P2.2+P2.3 as a single `kind='snmp'` integration** when open question
#1 is answered yes. The design fits the established external-sensor pattern
cleanly; the only genuinely new mechanic is the two-sample counter-delta
(§5), which is well-contained. Net-snmp shell-out keeps the dependency
footprint at zero new Python packages, consistent with `_probe_ping` and the
hand-parsing ethos of the existing integrations.

If open question #1 is "no SNMP gear on site," **park P2.2/P2.3** and treat
this note as the ready-to-go design for whenever the operator's network
hardware changes.
