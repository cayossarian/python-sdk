"""Tests for the general HA -> eBus semantic derivation (mapping fallback)."""

from __future__ import annotations

from ebus_sdk import PropertyDatatype, Unit

from ebus_sdk.ha import derive_spec, unit_for


def test_unit_for_known_and_scaled():
    assert unit_for("kWh") == (Unit.WATT_HOUR, 1000.0)
    assert unit_for("Wh") == (Unit.WATT_HOUR, 1.0)
    assert unit_for("A") == (Unit.AMPERE, 1.0)
    assert unit_for("mA") == (Unit.AMPERE, 0.001)


def test_unit_for_apparent_and_reactive():
    # IEC 80000-6 casing: apparent power/energy uppercase VA/VAh, reactive
    # power/energy lowercase var/varh; kilo variants scale by 1000.
    assert unit_for("VA") == (Unit.VOLT_AMPERE, 1.0)
    assert unit_for("kVA") == (Unit.VOLT_AMPERE, 1000.0)
    assert unit_for("VAh") == (Unit.VOLT_AMPERE_HOUR, 1.0)
    assert unit_for("var") == (Unit.VOLT_AMPERE_REACTIVE, 1.0)
    assert unit_for("kvar") == (Unit.VOLT_AMPERE_REACTIVE, 1000.0)
    assert unit_for("varh") == (Unit.VOLT_AMPERE_REACTIVE_HOUR, 1.0)
    assert unit_for("kvarh") == (Unit.VOLT_AMPERE_REACTIVE_HOUR, 1000.0)


def test_unit_string_values_follow_iec_casing():
    # The enum wire values are the exact IEC 80000-6 symbols.
    assert Unit.VOLT_AMPERE == "VA"
    assert Unit.VOLT_AMPERE_HOUR == "VAh"
    assert Unit.VOLT_AMPERE_REACTIVE == "var"
    assert Unit.VOLT_AMPERE_REACTIVE_HOUR == "varh"


def test_unit_for_unknown_is_none():
    assert unit_for("nonsense-unit") == (None, 1.0)
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
