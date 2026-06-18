"""Unit tests for `middleware.picker_scope` (the 0.6.49 helper).

Distinct from `test_picker_scope.py`, which exercises the service-
layer site filter (BUG-068 / #211). This file targets the
middleware-layer reusable validator + typed PickerScopeError
introduced in 0.6.49 to centralize the BUG-064 / BUG-068 / BUG-074
form-tamper defense across the schedules / rules / groups pickers.

Integration coverage of the schedules / rules / groups handlers
calling this helper lives in the QA suite.
"""

from __future__ import annotations

import pytest

from app.middleware.picker_scope import PickerScopeError, validate_picker_id


def test_accepts_id_in_visible_set():
    out = validate_picker_id(
        "dev_abc",
        {"dev_abc", "dev_def"},
        scope_label="Power source",
    )
    assert out == "dev_abc"


def test_strips_whitespace_before_compare():
    out = validate_picker_id(
        "  dev_abc\n",
        {"dev_abc"},
        scope_label="Power source",
    )
    assert out == "dev_abc"


def test_rejects_id_not_in_visible_set():
    with pytest.raises(PickerScopeError) as e:
        validate_picker_id(
            "dev_tampered",
            {"dev_abc", "dev_def"},
            scope_label="Power source",
        )
    assert e.value.scope_label == "Power source"
    assert e.value.submitted == "dev_tampered"
    assert "Power source could not be set" in str(e.value)
    assert "your visible fleet" in str(e.value)


def test_empty_value_required_raises():
    with pytest.raises(PickerScopeError) as e:
        validate_picker_id(
            "",
            {"dev_abc"},
            scope_label="Schedule device",
        )
    assert e.value.submitted is None


def test_empty_value_not_required_returns_none():
    out = validate_picker_id(
        "",
        {"dev_abc"},
        scope_label="Power source",
        required=False,
    )
    assert out is None


def test_none_value_not_required_returns_none():
    """`request.form.get(...)` returns None when the field is absent."""
    out = validate_picker_id(
        None,
        {"dev_abc"},
        scope_label="Power source",
        required=False,
    )
    assert out is None


def test_accepts_list_as_visible_ids():
    """Iterable contracts: list, generator, set all work."""
    out = validate_picker_id(
        "dev_abc",
        ["dev_abc", "dev_def"],
        scope_label="Power source",
    )
    assert out == "dev_abc"


def test_accepts_generator_as_visible_ids():
    """A one-shot generator must not exhaust itself on the membership
    check (would leak via a subsequent re-iteration in the caller)."""
    def _gen():
        yield "dev_abc"
        yield "dev_def"
    out = validate_picker_id(
        "dev_abc",
        _gen(),
        scope_label="Power source",
    )
    assert out == "dev_abc"


def test_rejects_when_visible_set_is_empty():
    with pytest.raises(PickerScopeError):
        validate_picker_id(
            "dev_abc",
            set(),
            scope_label="Power source",
        )


def test_error_message_uses_scope_label():
    """A handler attaching multiple pickers (e.g. a future scheduling
    UI with separate device + group selects) needs distinct error
    text per scope. The label flows verbatim into the message."""
    with pytest.raises(PickerScopeError) as e:
        validate_picker_id(
            "dev_tampered",
            {"dev_abc"},
            scope_label="Schedule target",
        )
    assert str(e.value).startswith("Schedule target could not be set")
