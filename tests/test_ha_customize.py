"""Tests for the eBus-aware default customizer (SDK-dn4, Phase 3)."""

from __future__ import annotations

from ebus_sdk.ha import (
    ebus_default_override,
    homie_description_to_ha,
    homie_property_to_component,
    to_config,
)
from ebus_sdk.ha import customize


def _component(node_id, prop_id, prop, node_type=None):
    return homie_property_to_component(
        "dev1", node_id, prop_id, prop, node_type=node_type, override=ebus_default_override
    )


def test_meter_imported_energy_is_total_increasing():
    comp = _component(
        "meter", "imported-energy", {"datatype": "float", "unit": "Wh"}, node_type="energy.ebus.capability.meter"
    )
    assert comp.device_class == "energy"
    assert comp.state_class == "total_increasing"


def test_meter_active_power():
    comp = _component(
        "meter", "active-power", {"datatype": "float", "unit": "W"}, node_type="energy.ebus.capability.meter"
    )
    assert comp.device_class == "power"
    assert comp.state_class == "measurement"


def test_battery_soc_resolves_ambiguous_percent():
    # A bare percent is ambiguous to inference (device_class None); the eBus
    # customizer recognizes battery/soc and nails device_class=battery.
    inferred = homie_property_to_component("dev1", "battery", "soc", {"datatype": "float", "unit": "%"})
    assert inferred.device_class is None

    comp = _component("battery", "soc", {"datatype": "float", "unit": "%"}, node_type="energy.ebus.capability.battery")
    assert comp.device_class == "battery"


def test_info_fields_are_diagnostic():
    comp = _component("info", "firmware-version", {"datatype": "string"}, node_type="energy.ebus.capability.info")
    assert comp.config["entity_category"] == "diagnostic"


def test_capability_from_node_id_when_no_type():
    # No node $type: fall back to the node id as the capability name.
    comp = _component("meter", "active-power", {"datatype": "float", "unit": "W"}, node_type=None)
    assert comp.device_class == "power"


def test_unrecognized_property_passes_through():
    # Unknown capability/property: inference result is left untouched.
    comp = _component(
        "widget", "blinkiness", {"datatype": "float", "unit": "W"}, node_type="energy.ebus.capability.widget"
    )
    assert comp.device_class == "power"  # from unit inference, not domain meta


def test_never_suppresses():
    comp = _component("info", "serial-number", {"datatype": "string"}, node_type="energy.ebus.capability.info")
    assert comp is not None


def test_usable_as_default_override_over_whole_device():
    description = {
        "name": "Panel",
        "nodes": {
            "meter": {
                "type": "energy.ebus.capability.meter",
                "properties": {
                    "imported-energy": {"datatype": "float", "unit": "Wh"},
                    "active-power": {"datatype": "float", "unit": "W"},
                },
            },
            "battery": {
                "type": "energy.ebus.capability.battery",
                "properties": {"soc": {"datatype": "float", "unit": "%"}},
            },
        },
    }
    device = homie_description_to_ha(description, "panel-1", override=ebus_default_override)
    assert device.components["meter_imported-energy"].state_class == "total_increasing"
    assert device.components["battery_soc"].device_class == "battery"


# --- typed-field routing (SDK-anu) ------------------------------------------


def test_customizer_value_template_lands_on_typed_field_and_emits(monkeypatch):
    # A table entry for a typed field (value_template) must land on the typed
    # HAComponent field, not be silently dropped into config where the pre-set
    # "{{ value }}" default shadows it on serialization.
    monkeypatch.setitem(
        customize._CAPABILITY_META,
        "meter",
        {**customize._CAPABILITY_META["meter"], "active-power": {"value_template": "{{ value | float * -1 }}"}},
    )
    description = {
        "name": "Panel",
        "nodes": {
            "meter": {
                "type": "energy.ebus.capability.meter",
                "properties": {"active-power": {"datatype": "float", "unit": "W"}},
            },
        },
    }
    device = homie_description_to_ha(description, "panel-1", override=ebus_default_override)
    comp = device.components["meter_active-power"]
    assert comp.value_template == "{{ value | float * -1 }}"  # routed to the typed field
    cfg = to_config(device)["components"]["meter_active-power"]
    assert cfg["value_template"] == "{{ value | float * -1 }}"  # and emitted, not shadowed


def test_customizer_can_set_default_entity_id(monkeypatch):
    # default_entity_id is likewise a typed field; a table must be able to set it.
    monkeypatch.setitem(
        customize._CAPABILITY_META,
        "meter",
        {**customize._CAPABILITY_META["meter"], "active-power": {"default_entity_id": "sensor.panel_power"}},
    )
    comp = _component(
        "meter", "active-power", {"datatype": "float", "unit": "W"}, node_type="energy.ebus.capability.meter"
    )
    assert comp.default_entity_id == "sensor.panel_power"
