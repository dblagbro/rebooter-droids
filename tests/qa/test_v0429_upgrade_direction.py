"""v0.4.29 — upgrade-button direction sanity tests.

Before v0.4.29 the per-device "Upgrade" button used the latest-by-
upload-time stable release and offered downgrades. e.g. a device
on `0.1.5-dev-central` was offered "Upgrade to 0.1.2-dev-central"
because 0.1.2 was the most recently uploaded blob.

v0.4.29 fixes both halves:

- `latest_stable_release_dict()` picks by **highest version**, not
  by upload time.
- A new `is_upgrade(target, current)` comparator powers the
  template gate and an extra server-side guard in the upgrade
  handler.

These tests cover the comparator in isolation; the integration
smoke (button never offered as a downgrade) is exercised by the
existing v0.4.28 test against the live cluster.
"""

from __future__ import annotations
import pytest

# v0.5.79: in the `-m ci` gate (P-QA gate-2 widening).
pytestmark = pytest.mark.ci



def test_is_upgrade_strict_numeric_order():
    # v0.5.4: import from _versions module so this test can run on
    # hosts without flask-limiter (e.g. a developer's Ubuntu box).
    # `app.services.devices` re-exports for back-compat, but pulls in
    # the full Flask runtime stack.
    from app.services._versions import is_upgrade

    # Strictly newer numeric prefix → True
    assert is_upgrade("0.1.5", "0.1.2")
    assert is_upgrade("0.2.0", "0.1.9")
    assert is_upgrade("1.0.0", "0.9.999")

    # Older numeric prefix → False (the bug case)
    assert not is_upgrade("0.1.2", "0.1.5")
    assert not is_upgrade("0.1.0", "0.1.1")
    assert not is_upgrade("0.0.9", "0.1.0")

    # Identical numeric prefix → False (same-version is not an upgrade)
    assert not is_upgrade("0.1.5", "0.1.5")


def test_is_upgrade_handles_suffix_labels():
    # v0.5.4: import from _versions module so this test can run on
    # hosts without flask-limiter (e.g. a developer's Ubuntu box).
    # `app.services.devices` re-exports for back-compat, but pulls in
    # the full Flask runtime stack.
    from app.services._versions import is_upgrade

    # Suffix shouldn't change the answer when numerics differ
    assert is_upgrade("0.1.5-dev-central", "0.1.2-dev-central")
    assert not is_upgrade("0.1.2-dev-central", "0.1.5-dev-central")

    # Cross-suffix at the SAME numeric prefix → False (intentional:
    # we don't treat `0.1.1-dev-central` → `0.1.1-dev-central-ui`
    # as an upgrade to avoid label-shuffle confusion)
    assert not is_upgrade("0.1.1-dev-central-ui", "0.1.1-dev-central")
    assert not is_upgrade("0.1.1-dev-central", "0.1.1-dev-central-ui")


def test_is_upgrade_handles_none_and_empty():
    # v0.5.4: import from _versions module so this test can run on
    # hosts without flask-limiter (e.g. a developer's Ubuntu box).
    # `app.services.devices` re-exports for back-compat, but pulls in
    # the full Flask runtime stack.
    from app.services._versions import is_upgrade

    # Either side missing → False (no upgrade offered)
    assert not is_upgrade(None, "0.1.5")
    assert not is_upgrade("0.1.5", None)
    assert not is_upgrade("", "0.1.5")
    assert not is_upgrade("0.1.5", "")
    assert not is_upgrade(None, None)


def test_version_sort_key_orders_real_fleet_versions():
    from app.services._versions import _version_sort_key  # v0.5.4 (see above)

    # The actual fleet on 2026-05-10:
    versions = [
        "0.1.1-dev-central",
        "0.1.1-dev-central-ui",
        "0.1.2-dev-central",
        "0.1.3-dev-central",
        "0.1.5-dev-central",
    ]
    sorted_versions = sorted(versions, key=_version_sort_key)
    assert sorted_versions == [
        "0.1.1-dev-central",
        "0.1.1-dev-central-ui",
        "0.1.2-dev-central",
        "0.1.3-dev-central",
        "0.1.5-dev-central",
    ]
    # And the max is the newest
    assert max(versions, key=_version_sort_key) == "0.1.5-dev-central"
