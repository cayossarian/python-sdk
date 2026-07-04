"""Tests for HA <-> eBus loop-avoidance guards (SDK-dn4)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ebus_sdk.homie import Controller, EBUS_HOMIE_DOMAIN, EBUS_HOMIE_VERSION_MAJOR
from ebus_sdk.ha import (
    EBUS_IMPORTED_EXTENSION,
    EBUS_SDK_ORIGIN_NAME,
    HA_ECOSYSTEM,
    IMPORTED_FROM_ATTRIBUTE,
    HaDiscoveryBridge,
    homie_description_to_ha,
    imported_extension,
    imported_from_attribute,
    imported_source,
    is_ebus_sdk_origin,
    is_imported,
    parse_device_config,
    to_config,
)


def _imported(source=HA_ECOSYSTEM, base=None):
    """A description marked imported: the extension + the imported-from attribute."""
    desc = dict(base if base is not None else DESCRIPTION)
    desc["extensions"] = [imported_extension()]
    desc.update(imported_from_attribute(source))
    return desc


_V = EBUS_HOMIE_VERSION_MAJOR
_D = EBUS_HOMIE_DOMAIN

DESCRIPTION = {
    "name": "Test Panel",
    "nodes": {
        "meter": {
            "type": "energy.ebus.capability.meter",
            "properties": {"power": {"datatype": "float", "unit": "W"}},
        },
    },
}


# -- Guard A: origin self-echo -----------------------------------------------


def test_emitted_config_carries_sdk_origin():
    device = homie_description_to_ha(DESCRIPTION, "panel-1")
    config = to_config(device)
    assert config["origin"]["name"] == EBUS_SDK_ORIGIN_NAME


def test_is_ebus_sdk_origin_true_for_round_tripped_config():
    # Emit -> parse: the parsed HADevice is recognizable as SDK-emitted.
    device = homie_description_to_ha(DESCRIPTION, "panel-1")
    reparsed = parse_device_config(to_config(device))
    assert is_ebus_sdk_origin(reparsed) is True


def test_is_ebus_sdk_origin_false_for_foreign_config():
    foreign = {
        "device": {"identifiers": ["x"]},
        "origin": {"name": "some-other-integration"},
        "components": {},
    }
    parsed = parse_device_config(foreign)
    assert is_ebus_sdk_origin(parsed) is False


def test_is_ebus_sdk_origin_handles_none():
    assert is_ebus_sdk_origin(None) is False


# -- Guard B: imported extension + imported-from attribute --------------------


def test_imported_extension_id_is_fixed():
    ext = imported_extension()
    assert ext.split(":", 1)[0] == EBUS_IMPORTED_EXTENSION  # no source in the id


def test_imported_from_attribute_shape():
    assert imported_from_attribute("ha") == {IMPORTED_FROM_ATTRIBUTE: "ha"}


def test_detection_and_source_from_attribute():
    desc = _imported("ha")
    assert is_imported(desc) is True
    assert imported_source(desc) == "ha"
    assert imported_source(_imported("zigbee")) == "zigbee"


def test_extension_without_source_is_imported_with_none_source():
    # Advertises the extension but names no source: imported, source unknown.
    only_ext = dict(DESCRIPTION, extensions=[imported_extension()])
    assert is_imported(only_ext) is True
    assert imported_source(only_ext) is None


def test_attribute_without_extension_still_counts():
    # A lenient reader: imported-from present even without the extension advert.
    only_attr = dict(DESCRIPTION, **imported_from_attribute("ha"))
    assert is_imported(only_attr) is True
    assert imported_source(only_attr) == "ha"


def test_is_imported_matches_base_id_regardless_of_version():
    assert is_imported({"extensions": ["energy.ebus.imported:9.9.9:[5.x]"]}) is True


def test_is_imported_false_without_marker():
    assert is_imported(DESCRIPTION) is False
    assert is_imported({"extensions": ["some.other.extension:1.0.0:[5.x]"]}) is False
    assert is_imported({}) is False
    assert imported_source(DESCRIPTION) is None


def test_is_imported_accepts_discovered_device_or_dict():
    class _Dev:
        description = _imported("ha")

    assert is_imported(_Dev()) is True
    assert imported_source(_Dev()) == "ha"


# -- Guard B wired into the bridge -------------------------------------------


def _make_controller(mock_paho):
    with patch("ebus_sdk.homie.MqttClient.from_config") as mock_from_config:
        mock_client = MagicMock()
        mock_client.sub_callbacks = {}
        mock_from_config.return_value = mock_client
        return Controller(mqtt_cfg={"host": "localhost", "port": 1883}), mock_client


def _discover(ctrl, device_id, description):
    ctrl._on_state_message(f"{_D}/{_V}/{device_id}/$state", b"ready")
    ctrl._on_description_message(device_id, f"{_D}/{_V}/{device_id}/$description", json.dumps(description).encode())


def _ha_topics(mock_client):
    return {c.args[0] for c in mock_client.publish.call_args_list if "/device/" in c.args[0]}


def test_bridge_skips_ha_imported_device_by_default(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    HaDiscoveryBridge(ctrl, reconcile_after=None).start()
    ctrl.start_discovery()
    _discover(ctrl, "mirror-1", _imported("ha"))
    _discover(ctrl, "native-1", DESCRIPTION)
    # Only the native device is exported; the HA-imported mirror is skipped.
    assert _ha_topics(mock_client) == {"homeassistant/device/native-1/config"}


def test_bridge_still_exports_device_imported_from_other_ecosystem(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    HaDiscoveryBridge(ctrl, reconcile_after=None).start()
    ctrl.start_discovery()
    _discover(ctrl, "zb-1", _imported("zigbee"))
    # Imported from Zigbee, not HA: exporting to HA is not a round trip.
    assert "homeassistant/device/zb-1/config" in _ha_topics(mock_client)


def test_bridge_can_opt_in_to_export_ha_imported(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    HaDiscoveryBridge(ctrl, skip_reimport=False, reconcile_after=None).start()
    ctrl.start_discovery()
    _discover(ctrl, "mirror-1", _imported("ha"))
    assert "homeassistant/device/mirror-1/config" in _ha_topics(mock_client)
