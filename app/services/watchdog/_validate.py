"""Per-kind probe + action validation for watchdog rules.

Split from the legacy single-file `services/watchdog.py` in v0.6.48.
The validation registry is the busiest churn axis of the watchdog
service — every new probe kind / action kind adds rules here.
Isolating it stops the rest of the service from drifting on
validation changes.

Public surface:
  - `WatchdogValidationError` — typed error; the blueprint catches it.
  - `validate_probe(probe)` / `validate_action(action, *, field=...)` —
    invoked from `_mutations.create_rule` + `_mutations.update_rule`.

External callers MUST import via `app.services.watchdog`, never this
module directly.
"""

from __future__ import annotations

from app.models.watchdog import (
    ACTION_KIND_BINDING,
    ACTION_KIND_SCENE,
    KNOWN_ACTION_KINDS,
    LEAF_ACTION_KINDS,
)


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


def validate_probe(probe: dict) -> None:
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
            if len(targets) > 10:
                raise WatchdogValidationError(
                    "probe.targets accepts at most 10 entries"
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

    if kind == "device_heartbeat_stale":
        _require("device_id")
        if "max_age_seconds" in probe:
            _require_int("max_age_seconds", low=30, high=86400)
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
        f"add a branch in services.watchdog._validate.validate_probe()"
    )


def validate_action(action: dict, *, field: str = "action") -> None:
    """v0.5.90 (Stage A) / v0.5.91 (Stage B): validate a rule action.

    A *leaf* action is one of `LEAF_ACTION_KINDS`. A `binding` action
    is level-triggered — it carries `on_active` + `on_clear`, each
    itself a fully-validated leaf (see
    `watchdog_runtime/_state.py::_binding_tick`). Bindings never nest.
    """
    if not isinstance(action, dict):
        raise WatchdogValidationError(f"{field} must be a JSON object")
    if action.get("kind") == ACTION_KIND_BINDING:
        for sub in ("on_active", "on_clear"):
            sub_action = action.get(sub)
            if not isinstance(sub_action, dict):
                raise WatchdogValidationError(
                    f"{field}.{sub} is required for a binding action and "
                    f"must be a JSON object"
                )
            if sub_action.get("kind") == ACTION_KIND_BINDING:
                raise WatchdogValidationError(
                    f"{field}.{sub} cannot itself be a binding"
                )
            _validate_leaf(sub_action, field=f"{field}.{sub}")
        return
    _validate_leaf(action, field=field)


def _validate_leaf(action: dict, *, field: str) -> None:
    """Validate one leaf action — its kind, plus any per-kind required
    fields. Only `apply_scene` (Stage B) carries extra structure: an
    `items` list, one entry per device, each a relay state and/or an
    `apply_config` payload."""
    kind = action.get("kind")
    if kind not in LEAF_ACTION_KINDS:
        raise WatchdogValidationError(
            f"{field}.kind must be one of {KNOWN_ACTION_KINDS}"
        )
    if kind != ACTION_KIND_SCENE:
        return
    # v0.5.92 (Stage C): an apply_scene action either references a
    # saved Scene by `scene_id`, or carries its device `items` inline.
    scene_id = action.get("scene_id")
    if scene_id is not None:
        if not isinstance(scene_id, str) or not scene_id.strip():
            raise WatchdogValidationError(
                f"{field}.scene_id must be a non-empty string"
            )
        return
    items = action.get("items")
    if not isinstance(items, list) or not items:
        raise WatchdogValidationError(
            f"{field}.items (or {field}.scene_id) is required for an "
            f"apply_scene action"
        )
    if len(items) > 50:
        raise WatchdogValidationError(
            f"{field}.items accepts at most 50 entries"
        )
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise WatchdogValidationError(
                f"{field}.items[{i}] must be a JSON object"
            )
        if not str(item.get("device_id") or "").strip():
            raise WatchdogValidationError(
                f"{field}.items[{i}].device_id is required"
            )
        relay = item.get("relay")
        config = item.get("config")
        if relay is not None and relay not in ("on", "off", "cycle"):
            raise WatchdogValidationError(
                f"{field}.items[{i}].relay must be 'on', 'off' or 'cycle'"
            )
        if config is not None and not isinstance(config, dict):
            raise WatchdogValidationError(
                f"{field}.items[{i}].config must be a JSON object"
            )
        if relay is None and not config:
            raise WatchdogValidationError(
                f"{field}.items[{i}] needs a 'relay' state or a 'config' payload"
            )
