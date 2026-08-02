"""Tests for the eBus/Homie -> Home Assistant discovery emitter (SDK-dn4).

Fixtures are SYNTHETIC Homie 5 `$description` documents, structurally faithful to
what `Device.description()` publishes, so the whole translation core is exercised
without a broker.
"""

from __future__ import annotations

from ebus_sdk.ha import (
    HAComponent,
    config_topic,
    device_class_for,
    homie_description_to_ha,
    homie_property_to_component,
    parse_device_config,
    platform_for,
    state_class_for,
    to_config,
)

# A Homie device with a read-only "meter" node and a settable "control" node,
# covering float+unit sensors, a read-only boolean, and settable
# switch/number/select controls.
DESCRIPTION = {
    "homie": "5.0",
    "version": 1,
    "type": "energy.ebus.device",
    "name": "Test Panel",
    "nodes": {
        "meter": {
            "name": "Meter",
            "type": "energy.ebus.capability.meter",
            "properties": {
                "imported-energy": {"name": "Imported Energy", "datatype": "float", "unit": "Wh"},
                "power": {"name": "Power", "datatype": "float", "unit": "W"},
                "online": {"name": "Online", "datatype": "boolean"},
            },
        },
        "control": {
            "name": "Control",
            "type": "energy.ebus.capability.control",
            "properties": {
                "enabled": {"name": "Enabled", "datatype": "boolean", "settable": True},
                "setpoint": {"name": "Setpoint", "datatype": "float", "unit": "W", "settable": True},
                "mode": {"name": "Mode", "datatype": "enum", "format": "auto,manual,off", "settable": True},
            },
        },
    },
    "children": [],
}


# --- inference helpers ------------------------------------------------------


def test_device_class_and_state_class_from_unit():
    assert device_class_for("Wh") == "energy"
    assert state_class_for("Wh") == "total_increasing"
    assert device_class_for("W") == "power"
    assert state_class_for("W") == "measurement"
    assert device_class_for("A") == "current"
    assert device_class_for("V") == "voltage"
    assert device_class_for("°C") == "temperature"


def test_percent_declines_to_guess_device_class():
    # A bare percent is ambiguous (battery / humidity / power_factor).
    assert device_class_for("%") is None


def test_unknown_unit_is_none():
    assert device_class_for(None) is None
    assert device_class_for("bogus") is None
    assert state_class_for(None) is None


def test_platform_for_readonly_and_settable():
    assert platform_for("float", False) == "sensor"
    assert platform_for("boolean", False) == "binary_sensor"
    assert platform_for("boolean", True) == "switch"
    assert platform_for("float", True) == "number"
    assert platform_for("integer", True) == "number"
    assert platform_for("enum", True) == "select"
    assert platform_for("string", True) == "text"


# --- single-property translation --------------------------------------------


def test_readonly_float_sensor_component():
    prop = {"name": "Power", "datatype": "float", "unit": "W"}
    comp = homie_property_to_component("dev1", "meter", "power", prop)
    assert comp.platform == "sensor"
    assert comp.unique_id == "dev1_meter_power"
    assert comp.key == "meter_power"
    assert comp.device_class == "power"
    assert comp.state_class == "measurement"
    assert comp.unit_of_measurement == "W"
    assert comp.state_topic == "ebus/5/dev1/meter/power"
    assert comp.value_template == "{{ value }}"
    assert "command_topic" not in comp.config


def test_energy_sensor_is_total_increasing():
    prop = {"datatype": "float", "unit": "Wh"}
    comp = homie_property_to_component("dev1", "meter", "e", prop)
    assert comp.device_class == "energy"
    assert comp.state_class == "total_increasing"


def test_readonly_boolean_is_binary_sensor_with_payloads():
    comp = homie_property_to_component("dev1", "meter", "online", {"datatype": "boolean"})
    assert comp.platform == "binary_sensor"
    assert comp.config["payload_on"] == "true"
    assert comp.config["payload_off"] == "false"
    assert "command_topic" not in comp.config


def test_settable_boolean_is_switch_with_command_topic():
    comp = homie_property_to_component("dev1", "control", "enabled", {"datatype": "boolean", "settable": True})
    assert comp.platform == "switch"
    assert comp.config["command_topic"] == "ebus/5/dev1/control/enabled/set"
    assert comp.config["payload_on"] == "true"
    assert comp.config["payload_off"] == "false"


def test_settable_float_is_number():
    comp = homie_property_to_component(
        "dev1", "control", "setpoint", {"datatype": "float", "unit": "W", "settable": True}
    )
    assert comp.platform == "number"
    assert comp.config["command_topic"] == "ebus/5/dev1/control/setpoint/set"
    assert comp.unit_of_measurement == "W"


def test_settable_enum_is_select_with_options():
    prop = {"datatype": "enum", "format": "auto,manual,off", "settable": True}
    comp = homie_property_to_component("dev1", "control", "mode", prop)
    assert comp.platform == "select"
    assert comp.config["options"] == ["auto", "manual", "off"]
    assert comp.config["command_topic"] == "ebus/5/dev1/control/mode/set"


def test_datetime_gets_timestamp_device_class():
    comp = homie_property_to_component("dev1", "meter", "last-seen", {"datatype": "datetime"})
    assert comp.platform == "sensor"
    assert comp.device_class == "timestamp"


# --- override hook seam ------------------------------------------------------


def test_override_can_modify_component():
    def force_battery(comp: HAComponent, ctx) -> HAComponent:
        if ctx.prop_id == "soc":
            comp.device_class = "battery"
        return comp

    comp = homie_property_to_component(
        "dev1", "battery", "soc", {"datatype": "float", "unit": "%"}, override=force_battery
    )
    assert comp.device_class == "battery"


def test_override_can_suppress_component():
    def drop_internal(comp, ctx):
        return None

    comp = homie_property_to_component("dev1", "meter", "debug", {"datatype": "string"}, override=drop_internal)
    assert comp is None


def test_override_receives_context():
    seen = {}

    def capture(comp, ctx):
        seen["node_type"] = ctx.node_type
        seen["settable"] = ctx.settable
        seen["unit"] = ctx.unit
        return comp

    homie_property_to_component(
        "dev1",
        "meter",
        "power",
        {"datatype": "float", "unit": "W"},
        node_type="energy.ebus.capability.meter",
        override=capture,
    )
    assert seen == {"node_type": "energy.ebus.capability.meter", "settable": False, "unit": "W"}


def test_override_receives_property_values():
    seen = {}

    def capture(comp, ctx):
        seen["property_values"] = ctx.property_values
        return comp

    values = {"info": {"space": "12"}, "meter": {"power": 42.0}}
    homie_property_to_component(
        "dev1",
        "meter",
        "power",
        {"datatype": "float", "unit": "W"},
        property_values=values,
        override=capture,
    )
    # The whole-device value map is threaded through, so a hook can read a
    # sibling property's value (here `info/space`) to derive stable identity.
    assert seen["property_values"] is values
    assert seen["property_values"]["info"]["space"] == "12"


def test_property_values_defaults_none_for_description_only_caller():
    seen = {}

    def capture(comp, ctx):
        seen["property_values"] = ctx.property_values
        return comp

    homie_property_to_component("dev1", "meter", "power", {"datatype": "float"}, override=capture)
    assert seen["property_values"] is None


def test_property_values_threaded_through_description_walk():
    def name_from_space(comp, ctx):
        # Realistic use: derive a stable name from a sibling `info/space` value
        # rather than the user-editable property name.
        if ctx.property_values:
            space = ctx.property_values.get("info", {}).get("space")
            if space is not None:
                comp.name = f"Circuit {space}"
        return comp

    device = homie_description_to_ha(
        DESCRIPTION, "test-panel", property_values={"info": {"space": "7"}}, override=name_from_space
    )
    # Every component saw the same device-wide value map.
    assert device.components["meter_power"].name == "Circuit 7"


# --- whole-device translation -----------------------------------------------


def test_homie_description_to_ha_walks_all_properties():
    device = homie_description_to_ha(DESCRIPTION, "test-panel")
    assert device.name == "Test Panel"
    assert device.identifiers == ["test-panel"]
    # 3 meter props + 3 control props
    assert len(device.components) == 6
    assert device.components["meter_power"].device_class == "power"
    assert device.components["control_mode"].platform == "select"


def test_device_availability_from_state_topic():
    device = homie_description_to_ha(DESCRIPTION, "test-panel")
    assert device.availability is not None
    src = device.availability.sources[0]
    assert src.topic == "ebus/5/test-panel/$state"
    assert "ready" in src.value_template


def test_via_device_from_parent():
    desc = dict(DESCRIPTION, parent="root-dev", root="root-dev")
    device = homie_description_to_ha(desc, "child-dev")
    assert device.via_device == "root-dev"


def test_override_suppression_drops_from_device():
    def drop_power(comp, ctx):
        return None if ctx.prop_id == "power" else comp

    device = homie_description_to_ha(DESCRIPTION, "test-panel", override=drop_power)
    assert "meter_power" not in device.components
    assert len(device.components) == 5


def test_info_capability_enriches_device_block():
    desc = {
        "name": "Panel",
        "nodes": {
            "info": {
                "name": "Info",
                "type": "energy.ebus.capability.info",
                "properties": {
                    "vendor-name": {"datatype": "string"},
                    "model": {"datatype": "string"},
                    "serial-number": {"datatype": "string"},
                    "firmware-version": {"datatype": "string"},
                },
            },
        },
    }
    values = {
        "info": {
            "vendor-name": "Acme",
            "model": "OmniPanel",
            "serial-number": "SN12345",
            "firmware-version": "1.2.3",
        }
    }
    device = homie_description_to_ha(desc, "panel-1", property_values=values)
    assert device.manufacturer == "Acme"
    assert device.model == "OmniPanel"
    assert device.serial_number == "SN12345"
    assert device.sw_version == "1.2.3"
    assert "SN12345" in device.identifiers


# --- serialization -----------------------------------------------------------


def test_to_config_structure():
    device = homie_description_to_ha(DESCRIPTION, "test-panel")
    config = to_config(device)
    assert config["device"]["identifiers"] == ["test-panel"]
    assert config["device"]["name"] == "Test Panel"
    assert config["origin"]["name"] == "ebus-sdk"
    assert config["availability_topic"] == "ebus/5/test-panel/$state"
    cmps = config["components"]
    assert cmps["meter_power"]["platform"] == "sensor"
    assert cmps["meter_power"]["device_class"] == "power"
    assert cmps["meter_power"]["state_topic"] == "ebus/5/test-panel/meter/power"
    assert cmps["control_enabled"]["command_topic"] == "ebus/5/test-panel/control/enabled/set"


def test_config_topic_uses_primary_id():
    device = homie_description_to_ha(DESCRIPTION, "test-panel")
    assert config_topic(device) == "homeassistant/device/test-panel/config"
    assert config_topic(device, discovery_prefix="ha") == "ha/device/test-panel/config"


def test_round_trip_through_parser_recovers_shared_fields():
    device = homie_description_to_ha(DESCRIPTION, "test-panel")
    config = to_config(device)
    reparsed = parse_device_config(config)
    # The parser recovers the neutral model's shared component fields.
    comp = reparsed.components["meter_power"]
    assert comp.platform == "sensor"
    assert comp.device_class == "power"
    assert comp.unit_of_measurement == "W"
    assert comp.state_topic == "ebus/5/test-panel/meter/power"
    assert reparsed.name == "Test Panel"


def test_default_entity_id_absent_when_unset():
    # No consumer sets it, so existing emitted payloads stay byte-identical.
    device = homie_description_to_ha(DESCRIPTION, "test-panel")
    config = to_config(device)
    assert "default_entity_id" not in config["components"]["meter_power"]


def test_def_ent_id_round_trips_to_long_form_and_emits():
    # Parse an abbreviated `def_ent_id`, then re-emit: the abbreviation collapses
    # to the long `default_entity_id` (as every other abbreviated field already
    # does), and it is emitted because the typed field is now set.
    raw = {
        "dev": {"ids": "panel-x", "name": "Panel X"},
        "o": {"name": "test"},
        "cmps": {
            "meter_power": {
                "p": "sensor",
                "uniq_id": "panel-x_meter_power",
                "def_ent_id": "sensor.stable_power",
                "stat_t": "ebus/5/panel-x/meter/power",
            },
        },
        "avty_t": "ebus/5/panel-x/$state",
    }
    parsed = parse_device_config(raw)
    assert parsed.components["meter_power"].default_entity_id == "sensor.stable_power"
    cmp = to_config(parsed)["components"]["meter_power"]
    assert cmp["default_entity_id"] == "sensor.stable_power"
    assert "def_ent_id" not in cmp  # normalized to the long form, emitted once


def test_default_entity_id_emitted_via_typed_field():
    # Set default_entity_id on the TYPED field only (empty config overlay), so it
    # can be emitted only through _COMPONENT_FIELDS, not the config fallback. This
    # guards the emit-side C2 change against a silent regression.
    def set_default_id(component, ctx):
        if ctx.prop_id == "power":
            component.default_entity_id = "sensor.stable_power"
        return component

    device = homie_description_to_ha(DESCRIPTION, "test-panel", override=set_default_id)
    assert "default_entity_id" not in device.components["meter_power"].config  # typed field, not config
    cmp = to_config(device)["components"]["meter_power"]
    assert cmp["default_entity_id"] == "sensor.stable_power"
