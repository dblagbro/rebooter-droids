"""Unit tests — `dashboard.derive_attention_items` (Tier-2 Feature 5).

The mobile-first dashboard pass adds a derived "Needs attention" list:
a pure function over a `stats()` payload (+ the per-request
`unregistered_active` count). It has no DB and no app context, so it is
tested directly — and the DB-backed `stats()` path is exercised once to
confirm the list is wired into the payload.
"""

from __future__ import annotations

from app.services.dashboard import derive_attention_items, stats


# ── pure derivation ────────────────────────────────────────────────────

def test_clean_fleet_yields_empty_list():
    s = {
        "devices_offline_with_history": 0,
        "devices_never_heartbeated": 0,
    }
    assert derive_attention_items(s, unregistered_active=0) == []


def test_offline_devices_produce_a_warn_item():
    s = {"devices_offline_with_history": 3, "devices_never_heartbeated": 0}
    items = derive_attention_items(s)
    assert len(items) == 1
    item = items[0]
    assert item["key"] == "devices_offline"
    assert item["count"] == 3
    assert item["severity"] == "warn"
    assert item["href_endpoint"] == "admin_ui.list_devices_page"
    assert "3 devices offline" in item["label"]


def test_singular_label_for_one_offline_device():
    items = derive_attention_items({"devices_offline_with_history": 1})
    assert "1 device offline" in items[0]["label"]


def test_never_heartbeated_produces_a_warn_item():
    items = derive_attention_items({"devices_never_heartbeated": 2})
    assert len(items) == 1
    assert items[0]["key"] == "devices_never_heartbeated"
    assert items[0]["severity"] == "warn"
    assert "2 devices never heartbeated" in items[0]["label"]


def test_unregistered_active_produces_an_info_item():
    items = derive_attention_items({}, unregistered_active=5)
    assert len(items) == 1
    assert items[0]["key"] == "unregistered_active"
    assert items[0]["severity"] == "info"
    assert items[0]["href_endpoint"] == "admin_ui.unregistered_devices_page"
    assert "5 unregistered auth attempts" in items[0]["label"]


def test_singular_label_for_one_unregistered_attempt():
    items = derive_attention_items({}, unregistered_active=1)
    assert "1 unregistered auth attempt" in items[0]["label"]
    assert "attempts" not in items[0]["label"]


def test_all_signals_present_are_severity_ordered():
    s = {
        "devices_offline_with_history": 4,
        "devices_never_heartbeated": 1,
    }
    items = derive_attention_items(s, unregistered_active=7)
    # warn items (offline, never) come before the info item (unregistered).
    assert [it["key"] for it in items] == [
        "devices_offline",
        "devices_never_heartbeated",
        "unregistered_active",
    ]
    assert [it["severity"] for it in items] == ["warn", "warn", "info"]


def test_none_and_missing_counts_are_treated_as_zero():
    s = {"devices_offline_with_history": None, "devices_never_heartbeated": None}
    assert derive_attention_items(s, unregistered_active=None) == []


# ── stats() wires the list into the payload ────────────────────────────

def test_stats_payload_includes_attention_items(hub_db):
    s = stats()
    assert "attention_items" in s
    # An empty fixture fleet has nothing offline / never-heartbeated.
    assert s["attention_items"] == []
