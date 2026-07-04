"""Tests for the vendor-neutral HA MQTT discovery parser.

Fixtures are SYNTHETIC (fake serials, fake names) but structurally faithful to
real Home Assistant device-based discovery payloads, so no captured device data
is kept in this repo.
"""

from __future__ import annotations

import json

from ebus_sdk.ha import (
    HADevice,
    HARemoval,
    extract_value_field,
    parse_device_config,
    read_field,
)

# An EKM-shaped device-based discovery payload with abbreviated keys, one shared
# state_topic, and three components differing only by their value_template field.
ABBREV_CONFIG = {
    "dev": {
        "ids": "test_meter_000000000001",
        "name": "Test Meter A",
        "mf": "Acme Metering",
        "sn": "000000000001",
        "hw": "v3",
        "sw": "1.2.3",
        "mdl": "OmniTest",
    },
    "o": {"name": "Test Push", "sw": "9.9.9", "url": "https://example.invalid/support"},
    "cmps": {
        "c_energy": {
            "p": "sensor",
            "dev_cla": "energy",
            "unit_of_meas": "kWh",
            "stat_cla": "total_increasing",
            "val_tpl": "{{ value_json.kWh_Tot }}",
            "uniq_id": "test_000000000001_kWh_Tot",
        },
        "c_power": {
            "p": "sensor",
            "dev_cla": "power",
            "unit_of_meas": "W",
            "val_tpl": "{{ value_json.RMS_Watts_Tot }}",
            "uniq_id": "test_000000000001_RMS_Watts_Tot",
        },
        "c_current": {
            "p": "sensor",
            "dev_cla": "current",
            "unit_of_meas": "A",
            "val_tpl": "{{ value_json.Amps_Ln_1 }}",
            "uniq_id": "test_000000000001_Amps_Ln_1",
        },
    },
    "stat_t": "testdata/test_meter_000000000001/state",
    "avty_t": "testdata/test_meter_000000000001/availability",
    "qos": 2,
}


def test_parses_device_metadata_from_abbreviated_keys():
    dev = parse_device_config(ABBREV_CONFIG)
    assert isinstance(dev, HADevice)
    assert dev.identifiers == ["test_meter_000000000001"]
    assert dev.serial_number == "000000000001"
    assert dev.name == "Test Meter A"
    assert dev.manufacturer == "Acme Metering"
    assert dev.model == "OmniTest"
    assert dev.hw_version == "v3"
    assert dev.sw_version == "1.2.3"
    assert dev.primary_id == "000000000001"
    assert dev.origin is not None
    assert dev.origin.name == "Test Push"
    assert dev.origin.sw_version == "9.9.9"
    assert dev.origin.support_url == "https://example.invalid/support"


def test_parses_components_with_value_fields_and_semantics():
    dev = parse_device_config(ABBREV_CONFIG)
    assert set(dev.components) == {"c_energy", "c_power", "c_current"}
    energy = dev.components["c_energy"]
    assert energy.platform == "sensor"
    assert energy.device_class == "energy"
    assert energy.unit_of_measurement == "kWh"
    assert energy.state_class == "total_increasing"
    assert energy.value_field == "kWh_Tot"
    assert energy.unique_id == "test_000000000001_kWh_Tot"
    assert dev.components["c_current"].value_field == "Amps_Ln_1"
    assert not energy.removed


def test_components_inherit_root_state_topic_and_availability():
    dev = parse_device_config(ABBREV_CONFIG)
    c = dev.components["c_power"]
    # No per-component state_topic -> inherits the device-root shared one.
    assert c.state_topic == "testdata/test_meter_000000000001/state"
    assert c.availability is dev.availability
    assert dev.availability is not None
    assert dev.availability.sources[0].topic == "testdata/test_meter_000000000001/availability"
    assert dev.availability.sources[0].payload_available == "online"
    assert dev.availability.sources[0].payload_not_available == "offline"


def test_accepts_raw_json_str_and_bytes():
    text = json.dumps(ABBREV_CONFIG)
    assert isinstance(parse_device_config(text), HADevice)
    assert isinstance(parse_device_config(text.encode()), HADevice)


def test_long_and_mixed_keys_are_equivalent():
    long_form = {
        "device": {"identifiers": ["x1"], "name": "N", "serial_number": "x1"},
        "origin": {"name": "o"},
        "components": {
            "c": {
                "platform": "sensor",
                "device_class": "voltage",
                "unit_of_measurement": "V",
                "value_template": "{{ value_json.RMS_Volts_Ln_1 }}",
                "unique_id": "x1_v",
            }
        },
        "state_topic": "t/state",
    }
    dev = parse_device_config(long_form)
    assert isinstance(dev, HADevice)
    assert dev.identifiers == ["x1"]
    assert dev.components["c"].device_class == "voltage"
    assert dev.components["c"].value_field == "RMS_Volts_Ln_1"


def test_identifiers_string_is_normalized_to_list():
    cfg = {"device": {"identifiers": "solo"}, "components": {}}
    dev = parse_device_config(cfg)
    assert dev.identifiers == ["solo"]
    assert dev.primary_id == "solo"


def test_base_topic_macro_expansion():
    cfg = {
        "~": "base/dev1",
        "device": {"identifiers": ["d"]},
        "state_topic": "~/state",
        "components": {
            "c": {
                "platform": "sensor",
                "state_topic": "~/c/state",
                "value_template": "{{ value_json.x }}",
                "unique_id": "u",
            }
        },
    }
    dev = parse_device_config(cfg)
    assert dev.state_topic == "base/dev1/state"
    assert dev.components["c"].state_topic == "base/dev1/c/state"


def test_availability_list_form_with_custom_payloads():
    cfg = {
        "device": {"identifiers": ["d"]},
        "availability": [
            {"t": "d/avail", "pl_avail": "up", "pl_not_avail": "down"},
        ],
        "availability_mode": "all",
        "components": {},
    }
    dev = parse_device_config(cfg)
    assert dev.availability is not None
    assert dev.availability.mode == "all"
    src = dev.availability.sources[0]
    assert src.topic == "d/avail"
    assert src.payload_available == "up"
    assert src.payload_not_available == "down"


def test_empty_payload_is_removal():
    assert isinstance(parse_device_config("", object_id="dev1"), HARemoval)
    assert isinstance(parse_device_config("   ", object_id="dev1"), HARemoval)
    assert isinstance(parse_device_config({}, object_id="dev1"), HARemoval)
    assert isinstance(parse_device_config(None, object_id="dev1"), HARemoval)
    assert parse_device_config("", object_id="dev1").object_id == "dev1"


def test_platform_only_component_is_marked_removed():
    cfg = {
        "device": {"identifiers": ["d"]},
        "components": {
            "gone": {"p": "sensor"},
            "kept": {"p": "sensor", "value_template": "{{ value_json.y }}", "unique_id": "u"},
        },
    }
    dev = parse_device_config(cfg)
    assert dev.components["gone"].removed is True
    assert dev.components["kept"].removed is False


def test_migrate_discovery_is_not_a_config():
    assert parse_device_config({"migrate_discovery": True}) is None


def test_invalid_or_non_device_payloads_return_none():
    assert parse_device_config("{not json") is None
    assert parse_device_config("[1,2,3]") is None  # valid JSON, not a dict
    assert parse_device_config({"components": {}}) is None  # no device block


def test_extract_value_field_variants():
    assert extract_value_field("{{ value_json.kWh_Tot }}") == ("kWh_Tot", False)
    assert extract_value_field("{{ value_json.Timer1.Arm }}") == ("Timer1.Arm", False)
    assert extract_value_field("{{ value_json['kWh_Tot'] }}") == ("kWh_Tot", False)
    assert extract_value_field('{{ value_json["a"]["b"] }}') == ("a.b", False)
    assert extract_value_field("{{ (value_json.x | round(2)) }}") == ("x", False)
    assert extract_value_field("{{ as_datetime(value) }}") == (None, True)
    assert extract_value_field("static text") == (None, False)
    assert extract_value_field(None) == (None, False)


def test_read_field_nested_and_missing():
    payload = {"kWh_Tot": 22527.5, "Nested": {"Inner": 7}}
    assert read_field(payload, "kWh_Tot") == 22527.5
    assert read_field(payload, "Nested.Inner") == 7
    assert read_field(payload, "Nested.Missing") is None
    assert read_field(payload, "absent") is None
    assert read_field(payload, None) is None
