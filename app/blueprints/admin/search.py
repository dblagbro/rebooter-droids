"""Search endpoint for the ⌘K command palette (0.6.28 PR-4).

Returns a flat list of matchable items the palette presents:
  - device  → "<display_name>" with subtitle "<mac> · <ip>"
  - action  → e.g. "Reboot kitchen-modem" → device_restart for that device
  - page    → "Devices", "Rules", "History", "Settings", "Firmware"

The palette does its own fuzzy-match client-side; this endpoint only
returns a pre-filtered candidate list (with a simple substring match
on q so we don't ship the whole device list every keystroke). Action
items are permission-gated server-side — a viewer never sees verbs
they can't fire.
"""
from __future__ import annotations

from flask import g, request, url_for

from app.blueprints.admin import admin_api_bp
from app.middleware.admin_auth import admin_required_api
from app.middleware.response import ok
from app.services.devices import list_devices as svc_list_devices


# Hardcoded page list — small enough to keep here. Add a route + label
# when a new page is worth bookmarking. URLs use url_for so script_root
# is correct behind the /rebooter prefix.
def _pages() -> list[dict]:
    return [
        {"label": "Devices",   "url": url_for("admin_ui.list_devices_page")},
        {"label": "Rules",     "url": url_for("admin_ui.rules_page")},
        {"label": "History",   "url": url_for("admin_ui.history_page")},
        {"label": "Power",     "url": url_for("admin_ui.fleet_power_page")},
        {"label": "Settings",  "url": url_for("admin_ui.settings_page")},
        # 0.6.33 audit-found bug: was admin_ui.firmware_page (doesn't
        # exist) — the real endpoint is admin_ui.list_firmware_page.
        # url_for() raised BuildError → /api/v1/admin/search 500'd
        # every time the ⌘K palette was opened, breaking it entirely.
        {"label": "Firmware",  "url": url_for("admin_ui.list_firmware_page")},
    ]


@admin_api_bp.get("/search")
@admin_required_api
def cmdk_search():
    """0.6.28 PR-4: ⌘K palette feeder.

    Query param `q` is the operator's typed text. If absent or empty we
    return all candidates (capped) — useful when the palette opens with
    nothing typed yet. The client does final ranking; the server's job
    is just to stage a reasonable candidate set.
    """
    q = (request.args.get("q") or "").strip().lower()
    role = (g.current_user.role if getattr(g, "current_user", None) else "viewer")
    can_act = role in ("super_admin", "admin")

    devices = svc_list_devices(include_qa_fixtures=False)
    items: list[dict] = []

    # Pages first — they're the cheapest match and the operator hits ⌘K
    # most often to navigate.
    for p in _pages():
        if not q or q in p["label"].lower():
            items.append({
                "kind": "page",
                "label": p["label"],
                "subtitle": p["url"],
                "url": p["url"],
            })

    # Devices + per-device actions. Trim heavy fields the palette doesn't
    # display so the response stays small (~1KB per 10 devices).
    for d in devices:
        name = d.get("display_name") or d.get("id")
        mac = d.get("mac_address") or ""
        ip = d.get("local_ip") or ""
        haystack = " ".join(s for s in (name, mac, ip, d.get("id", "")) if s).lower()
        if q and q not in haystack:
            continue
        items.append({
            "kind": "device",
            "label": name,
            "subtitle": " · ".join(s for s in (mac, ip) if s),
            "url": url_for("admin_ui.device_detail_page", device_id=d["id"]),
            "device_id": d["id"],
        })
        if can_act:
            relay_now = d.get("latest_relay_on")
            verbs: list[tuple[str, str]] = []
            if relay_now is True:
                verbs.append(("Turn off", "relay_off"))
            elif relay_now is False:
                verbs.append(("Turn on", "relay_on"))
            verbs.append(("Reboot", "device_restart"))
            for verb_label, cmd_type in verbs:
                items.append({
                    "kind": "action",
                    "label": f"{verb_label} {name}",
                    "subtitle": cmd_type,
                    "device_id": d["id"],
                    "command_type": cmd_type,
                    "post_url": url_for("admin_ui.device_send_command", device_id=d["id"]),
                })

    # Cap to keep the wire small. 200 items × ~150 bytes = ~30KB; plenty
    # of headroom for the palette to filter client-side.
    return ok({"items": items[:200]})
