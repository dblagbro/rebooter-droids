# Admin Guide

A walkthrough of the admin web app at
<https://www.voipguru.org/rebooter/app/>.

## Sign in

Use the bootstrap admin email and password set in
`REBOOTER_BOOTSTRAP_ADMIN_EMAIL` / `REBOOTER_BOOTSTRAP_ADMIN_PASSWORD` for
the first login. Rotate it as soon as you get in.

## Dashboard

Top of page shows: total devices, online (green), offline (red). Online is
defined as: a heartbeat in the last 180 s.

## Enrolling a device

1. Open **Enrollment Tokens**.
2. Click *Mint token*. Add an optional display-name hint (e.g.
   `Branch 14 Router`) so the device list will pre-populate the name.
3. Copy the displayed token — it appears **once** and never again.
4. Hand the token to whoever is configuring the unit and have them paste
   it into the device's `central_enrollment_token` setting.
5. The device will register itself within a minute and appear in
   **Devices**.

If a token leaks before being redeemed, mint a fresh one — single-use
tokens cannot be revoked, but they expire after 24 hours by default.

## Devices

The Devices page lists all registered units with their state and last
heartbeat. Click a device for the detail page:

- **Heartbeat** — most recent telemetry.
- **Send command** — buttons for `relay_on`, `relay_off`, `relay_toggle`,
  `device_restart`, `check_firmware`. Plus a `relay_cycle` form with
  `power_off_seconds` and `post_reboot_holdoff_seconds`.
- **Pending commands** — what is queued for the device.
- **Recent events** — last 20 events ordered by event timestamp.
- **Edit metadata** — change display name, notes, central-management on/off.

## Groups

Group devices to issue one fan-out command. *Send command to all members*
materialises one command row per device — each will be returned to that
specific device on its next poll.

## Sites

Sites are a coarser organisational grouping (e.g. "Atlanta", "Branch 14").
Currently used for filtering devices and as a target for firmware
deployments.

## Firmware

1. **Upload** — choose a version string (e.g. `0.1.2`), a channel
   (`dev|beta|stable`), and the binary. If you provide a SHA-256, the
   server will verify it before persisting; if not, it will store the
   server-computed hash.
2. **Deploy** — pick a release, a target type (`device`, `group`, `site`,
   or `all_devices`), and the matching target id. *Force* re-deploys even
   if the device is already on that version.
3. **Deployments** table shows live rollout counts: `pending` (assignment
   created), `delivered` (device fetched the assignment via
   `GET /device/firmware`), `completed` (device reported success),
   `failed`, `superseded` (a later deployment replaced this one).

Devices download firmware from `https://www.voipguru.org/rebooter/firmware/…`
directly via nginx — no app round-trip, no auth challenge, but the device
**must** verify the SHA-256 before flashing.

## Events

The Events browser supports filtering by device id, type, and a time
window. Useful when triaging watchdog incidents across the fleet.

## Mobile/API auth

`POST /api/v1/auth/login` with JSON `{ email, password }` returns a
`Bearer` access token (8 h) and refresh token (14 d) usable from any
client. The web app uses cookie-based sessions; mobile clients should use
the bearer token.
