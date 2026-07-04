"""Tests for the general HA -> eBus semantic derivation (mapping fallback)."""

from __future__ import annotations

from ebus_sdk import PropertyDatatype, Unit

from ebus_sdk.ha import derive_spec, unit_for


def test_unit_for_known_and_scaled():
    assert unit_for("kWh") == (Unit.WATT_HOUR, 1000.0)
    assert unit_for("Wh") == (Unit.WATT_HOUR, 1.0)
    assert unit_for("A") == (Unit.AMPERE, 1.0)
    assert unit_for("mA") == (Unit.AMPERE, 0.001)


def test_unit_for_unknown_is_none():
    assert unit_for("VA") == (None, 1.0)  # sdk has no apparent-power unit yet
    assert unit_for(None) == (None, 1.0)


def test_derive_spec_energy_carries_datatype_unit_scale():
    spec = derive_spec("energy", "kWh", "kWh_Tot")
    assert spec.capability == "meter"
    assert spec.prop_id == "kwh-tot"  # sanitized source field name
    assert spec.datatype == PropertyDatatype.FLOAT
    assert spec.unit == Unit.WATT_HOUR
    assert spec.scale == 1000.0


def test_derive_spec_unitless_defaults_to_string():
    spec = derive_spec(None, None, "Some_Label")
    assert spec.datatype == PropertyDatatype.STRING
    assert spec.unit is None


def test_derive_spec_empty_field_returns_none():
    assert derive_spec("energy", "kWh", "") is None
