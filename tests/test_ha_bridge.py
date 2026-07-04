"""Tests for HaDiscoveryBridge (SDK-dn4, Phase 2).

Drives a mock-MQTT Controller through discover / describe / remove and asserts
the bridge publishes exactly the HA discovery topics/messages expected. No real
broker: publishes are captured off the mocked MQTT client.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ebus_sdk.homie import Controller, EBUS_HOMIE_DOMAIN, EBUS_HOMIE_VERSION_MAJOR
from ebus_sdk.ha import HaDiscoveryBridge, homie_description_to_ha, to_config

_V = EBUS_HOMIE_VERSION_MAJOR
_D = EBUS_HOMIE_DOMAIN

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
                "power": {"name": "Power", "datatype": "float", "unit": "W"},
                "debug": {"name": "Debug", "datatype": "string"},
            },
        },
    },
    "children": [],
}


def _make_controller(mock_paho):
    with patch("ebus_sdk.homie.MqttClient.from_config") as mock_from_config:
        mock_client = MagicMock()
        mock_client.sub_callbacks = {}
        mock_from_config.return_value = mock_client
        ctrl = Controller(mqtt_cfg={"host": "localhost", "port": 1883})
        return ctrl, mock_client


def _discover(ctrl, device_id, description=DESCRIPTION, state="ready"):
    """Feed the controller a $state then a $description for device_id."""
    ctrl._on_state_message(f"{_D}/{_V}/{device_id}/$state", state.encode())
    ctrl._on_description_message(device_id, f"{_D}/{_V}/{device_id}/$description", json.dumps(description).encode())


def _ha_publishes(mock_client):
    """All (topic, payload, retain) the bridge published to the HA prefix."""
    out = []
    for call in mock_client.publish.call_args_list:
        topic = call.args[0]
        payload = call.args[1]
        if "/device/" in topic and topic.startswith("homeassistant/"):
            out.append((topic, payload, call.kwargs.get("retain")))
    return out


# -- publish on describe -----------------------------------------------------


def test_publishes_retained_config_on_description(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    bridge = HaDiscoveryBridge(ctrl)
    bridge.start()
    ctrl.start_discovery()

    _discover(ctrl, "panel-1")

    published = _ha_publishes(mock_client)
    assert len(published) == 1
    topic, payload, retain = published[0]
    assert topic == "homeassistant/device/panel-1/config"
    assert retain is True
    config = json.loads(payload)
    assert config["device"]["identifiers"] == ["panel-1"]
    assert config["components"]["meter_power"]["device_class"] == "power"
    assert config["availability_topic"] == f"{_D}/{_V}/panel-1/$state"


def test_custom_discovery_prefix(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    HaDiscoveryBridge(ctrl, discovery_prefix="ha-test").start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    topics = [c.args[0] for c in mock_client.publish.call_args_list if "/device/" in c.args[0]]
    assert "ha-test/device/panel-1/config" in topics


def test_republishes_on_description_change(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    HaDiscoveryBridge(ctrl).start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    updated = json.loads(json.dumps(DESCRIPTION))
    updated["nodes"]["meter"]["properties"]["voltage"] = {"datatype": "float", "unit": "V"}
    ctrl._on_description_message("panel-1", f"{_D}/{_V}/panel-1/$description", json.dumps(updated).encode())

    published = _ha_publishes(mock_client)
    assert len(published) == 2
    last = json.loads(published[-1][1])
    assert "meter_voltage" in last["components"]


def test_publishes_already_known_device_on_start(mock_paho):
    # Description arrives BEFORE the bridge starts (e.g. retained). start()
    # should publish it immediately.
    ctrl, mock_client = _make_controller(mock_paho)
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    bridge = HaDiscoveryBridge(ctrl)
    bridge.start()
    assert any(t == "homeassistant/device/panel-1/config" for t, _, _ in _ha_publishes(mock_client))


# -- clear on removal --------------------------------------------------------


def test_clears_config_on_device_removed(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    HaDiscoveryBridge(ctrl).start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    # Empty retained $state == device removed.
    ctrl._on_state_message(f"{_D}/{_V}/panel-1/$state", b"")

    published = _ha_publishes(mock_client)
    topic, payload, retain = published[-1]
    assert topic == "homeassistant/device/panel-1/config"
    assert payload == ""  # empty retained payload clears the discovery
    assert retain is True


def test_lost_does_not_clear_by_default(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    HaDiscoveryBridge(ctrl).start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    ctrl._on_state_message(f"{_D}/{_V}/panel-1/$state", b"lost")
    # Only the initial publish; the availability template handles offline, so
    # nothing is cleared.
    clears = [p for _, p, _ in _ha_publishes(mock_client) if p == ""]
    assert clears == []


def test_clear_on_lost_when_enabled(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    HaDiscoveryBridge(ctrl, clear_on_lost=True).start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    ctrl._on_state_message(f"{_D}/{_V}/panel-1/$state", b"lost")
    clears = [(t, p) for t, p, _ in _ha_publishes(mock_client) if p == ""]
    assert clears == [("homeassistant/device/panel-1/config", "")]


def test_republishes_after_recovery_from_lost(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    HaDiscoveryBridge(ctrl, clear_on_lost=True).start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    ctrl._on_state_message(f"{_D}/{_V}/panel-1/$state", b"lost")  # cleared
    ctrl._on_state_message(f"{_D}/{_V}/panel-1/$state", b"ready")  # recovered
    topics = [(t, p) for t, p, _ in _ha_publishes(mock_client)]
    # publish, clear, publish
    assert topics[-1] == ("homeassistant/device/panel-1/config", topics[0][1])


# -- device selection --------------------------------------------------------


def test_include_allowlist_filters(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    HaDiscoveryBridge(ctrl, include=["panel-1"]).start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    _discover(ctrl, "panel-2")
    topics = {t for t, _, _ in _ha_publishes(mock_client)}
    assert topics == {"homeassistant/device/panel-1/config"}


def test_device_filter_predicate(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    HaDiscoveryBridge(ctrl, device_filter=lambda d: d.device_id.endswith("-keep")).start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-keep")
    _discover(ctrl, "panel-drop")
    topics = {t for t, _, _ in _ha_publishes(mock_client)}
    assert topics == {"homeassistant/device/panel-keep/config"}


# -- per-device mapping resolution -------------------------------------------


def _drop_debug(comp, ctx):
    return None if ctx.prop_id == "debug" else comp


def test_per_device_override_applied(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    HaDiscoveryBridge(ctrl, overrides={"panel-1": _drop_debug}).start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    config = json.loads(_ha_publishes(mock_client)[-1][1])
    assert "meter_debug" not in config["components"]
    assert "meter_power" in config["components"]


def test_default_override_used_when_no_specific(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    HaDiscoveryBridge(ctrl, default_override=_drop_debug).start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-2")  # no per-device entry -> falls back to default
    config = json.loads(_ha_publishes(mock_client)[-1][1])
    assert "meter_debug" not in config["components"]


def test_override_for_resolver(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    mapping = {"panel-1": _drop_debug}
    HaDiscoveryBridge(ctrl, override_for=lambda d: mapping.get(d.device_id)).start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    _discover(ctrl, "panel-2")
    by_topic = {t: json.loads(p) for t, p, _ in _ha_publishes(mock_client) if p}
    assert "meter_debug" not in by_topic["homeassistant/device/panel-1/config"]["components"]
    assert "meter_debug" in by_topic["homeassistant/device/panel-2/config"]["components"]


def test_overrides_win_over_default(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    keep_all = lambda comp, ctx: comp  # noqa: E731
    HaDiscoveryBridge(ctrl, overrides={"panel-1": keep_all}, default_override=_drop_debug).start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    config = json.loads(_ha_publishes(mock_client)[-1][1])
    # panel-1's specific hook keeps everything, despite the debug-dropping default.
    assert "meter_debug" in config["components"]


# -- lifecycle ---------------------------------------------------------------


def test_start_is_idempotent(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    bridge = HaDiscoveryBridge(ctrl)
    bridge.start()
    bridge.start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    assert len(_ha_publishes(mock_client)) == 1


def test_stop_restores_prior_callback_and_chains(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    seen = []
    ctrl.set_on_description_received_callback(lambda dev: seen.append(dev.device_id))
    bridge = HaDiscoveryBridge(ctrl)
    bridge.start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    # Pre-existing callback still runs (chained).
    assert seen == ["panel-1"]
    bridge.stop()
    # After stop, the bridge no longer publishes, prior callback restored.
    before = len(_ha_publishes(mock_client))
    _discover(ctrl, "panel-2")
    assert len(_ha_publishes(mock_client)) == before
    assert seen == ["panel-1", "panel-2"]


def test_clear_on_stop_clears_published(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    bridge = HaDiscoveryBridge(ctrl, clear_on_stop=True)
    bridge.start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    bridge.stop()
    clears = [(t, p) for t, p, _ in _ha_publishes(mock_client) if p == ""]
    assert clears == [("homeassistant/device/panel-1/config", "")]


def test_clear_all_removes_published_without_unwiring(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    bridge = HaDiscoveryBridge(ctrl, reconcile_after=None)
    bridge.start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    _discover(ctrl, "panel-2")
    assert bridge.clear_all() == 2
    clears = {t for t, p, _ in _ha_publishes(mock_client) if p == ""}
    assert clears == {"homeassistant/device/panel-1/config", "homeassistant/device/panel-2/config"}
    # Still wired: a later discovery still publishes.
    assert bridge._started
    _discover(ctrl, "panel-3")
    assert "homeassistant/device/panel-3/config" in {t for t, p, _ in _ha_publishes(mock_client) if p != ""}


def test_stop_leaves_configs_by_default(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    bridge = HaDiscoveryBridge(ctrl, reconcile_after=None)
    bridge.start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")
    bridge.stop()
    # Graceful stop: nothing cleared, so HA keeps the entity across a restart.
    assert [p for _, p, _ in _ha_publishes(mock_client) if p == ""] == []


def test_context_manager_starts_and_stops(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    ctrl.start_discovery()
    with HaDiscoveryBridge(ctrl, reconcile_after=None, clear_on_stop=True) as bridge:
        assert bridge._started
        _discover(ctrl, "panel-1")
        assert "homeassistant/device/panel-1/config" in {t for t, _, _ in _ha_publishes(mock_client)}
    # On exit, stop() ran: clear_on_stop cleared the published config.
    clears = [(t, p) for t, p, _ in _ha_publishes(mock_client) if p == ""]
    assert ("homeassistant/device/panel-1/config", "") in clears


def test_stop_unsubscribes_config_wildcard_and_cancels_timer(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    bridge = HaDiscoveryBridge(ctrl, reconcile_after=100)
    bridge.start()
    assert bridge._reconcile_timer is not None
    bridge.stop()
    assert bridge._reconcile_timer is None
    mock_client.unsubscribe.assert_any_call("homeassistant/device/+/config")


# -- reconciliation (orphan cleanup) -----------------------------------------


def _sdk_config(device_id):
    """A retained config as THIS SDK would emit it (carries our origin)."""
    return json.dumps(to_config(homie_description_to_ha(DESCRIPTION, device_id)))


def test_reconcile_clears_orphaned_sdk_config(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    bridge = HaDiscoveryBridge(ctrl, reconcile_after=None)
    bridge.start()
    ctrl.start_discovery()
    # A retained SDK-emitted config for a device that will NOT be discovered.
    bridge._on_config_seen("homeassistant/device/ghost-1/config", _sdk_config("ghost-1"))
    cleared = bridge.reconcile()
    assert cleared == 1
    clears = [(t, p) for t, p, _ in _ha_publishes(mock_client) if p == ""]
    assert ("homeassistant/device/ghost-1/config", "") in clears


def test_reconcile_keeps_live_device_config(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    bridge = HaDiscoveryBridge(ctrl, reconcile_after=None)
    bridge.start()
    ctrl.start_discovery()
    _discover(ctrl, "panel-1")  # published, so it is live
    topic = "homeassistant/device/panel-1/config"
    bridge._on_config_seen(topic, _sdk_config("panel-1"))
    assert bridge.reconcile() == 0
    assert topic not in [t for t, p, _ in _ha_publishes(mock_client) if p == ""]


def test_reconcile_ignores_foreign_config(mock_paho):
    ctrl, mock_client = _make_controller(mock_paho)
    bridge = HaDiscoveryBridge(ctrl, reconcile_after=None)
    bridge.start()
    ctrl.start_discovery()
    # A config from another integration (not our origin): never tracked/cleared.
    foreign = json.dumps({"device": {"identifiers": ["x"]}, "origin": {"name": "zwave2mqtt"}, "components": {}})
    bridge._on_config_seen("homeassistant/device/foreign/config", foreign)
    assert "homeassistant/device/foreign/config" not in bridge._observed_configs
    assert bridge.reconcile() == 0


def test_config_seen_empty_payload_drops_entry(mock_paho):
    ctrl, _ = _make_controller(mock_paho)
    bridge = HaDiscoveryBridge(ctrl, reconcile_after=None)
    bridge.start()
    topic = "homeassistant/device/ghost-1/config"
    bridge._on_config_seen(topic, _sdk_config("ghost-1"))
    assert topic in bridge._observed_configs
    bridge._on_config_seen(topic, "")  # a cleared topic
    assert topic not in bridge._observed_configs
