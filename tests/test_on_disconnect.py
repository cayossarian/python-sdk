"""Tests for the consumer on_disconnect hook (SDK-al5).

The SDK normalizes the transport (paho) integer reason code to a
transport-neutral ``clean: bool`` at its boundary and never surfaces a paho
type/value, so no transitive-dependency detail leaks into an eBus consumer.
"""

from unittest.mock import MagicMock, patch

from ebus_sdk import Controller, Device
from ebus_sdk.homie import _dispatch_disconnect


def test_dispatch_normalizes_rc_to_clean_bool():
    seen = []
    _dispatch_disconnect(lambda clean: seen.append(clean), 0, "test")  # MQTT_ERR_SUCCESS -> clean
    _dispatch_disconnect(lambda clean: seen.append(clean), 7, "test")  # MQTT_ERR_CONN_LOST -> not clean
    assert seen == [True, False]


def test_dispatch_none_callback_is_noop():
    _dispatch_disconnect(None, 0, "test")  # must not raise


def test_dispatch_is_best_effort():
    def boom(clean):
        raise ValueError("consumer boom")

    # A raising consumer callback must be swallowed (it runs on the network loop).
    _dispatch_disconnect(boom, 7, "test")


def test_dispatch_accepts_reasoncode_like_object():
    # Future-proofing: a paho v5 ReasonCode exposes .value; clean derives from it
    # without the SDK contract (or a consumer) ever touching a paho type.
    class RC:
        def __init__(self, value):
            self.value = value

    seen = []
    _dispatch_disconnect(lambda clean: seen.append(clean), RC(0), "test")
    _dispatch_disconnect(lambda clean: seen.append(clean), RC(16), "test")
    assert seen == [True, False]


def test_device_on_disconnect_receives_clean_bool():
    seen = []
    # Transport-free (mqtt_cfg=None): constructs without a broker; drive the
    # handler directly as ebus-mqtt-client would (with a paho int rc).
    dev = Device(id="d1", mqtt_cfg=None, on_disconnect=lambda clean: seen.append(clean))
    dev._handle_disconnect(0)  # orderly disconnect
    dev._handle_disconnect(7)  # unexpected drop
    assert seen == [True, False]


def test_device_on_disconnect_default_is_noop():
    dev = Device(id="d2", mqtt_cfg=None)
    dev._handle_disconnect(7)  # no callback set -> no error


def test_controller_on_disconnect_setter_and_routing():
    seen = []
    # Bring-your-own client (truthy) short-circuits _connect_broker: no broker.
    c = Controller(mqttc=object())
    c.set_on_disconnect_callback(lambda clean: seen.append(clean))
    c._handle_disconnect(0)
    c._handle_disconnect(4)  # MQTT_ERR_NO_CONN -> not clean
    assert seen == [True, False]


# -- transport wiring: the from_config kwarg must actually be registered -------
# These guard the integration seam the direct-dispatch tests above bypass: if a
# future edit drops/renames on_disconnect_callback in either from_config call,
# the hook silently dies. Here we capture the wired callback and drive it as
# ebus-mqtt-client would (with a paho int rc), asserting it routes to the
# consumer with the normalized clean bool. (Mirrors test_on_connect_callback_set.)


def test_device_wires_on_disconnect_into_transport(mock_paho):
    seen = []
    with patch("ebus_sdk.homie.MqttClient.from_config") as mock_from_config:
        mock_from_config.return_value = MagicMock(is_running=True)
        Device(
            id="panel-1",
            mqtt_cfg={"host": "localhost", "port": 1883},
            on_disconnect=lambda clean: seen.append(clean),
        )
        wired = mock_from_config.call_args.kwargs["on_disconnect_callback"]
    assert wired is not None
    wired(0)  # orderly disconnect
    wired(7)  # unexpected drop
    assert seen == [True, False]


def test_controller_wires_on_disconnect_into_transport(mock_paho):
    seen = []
    with patch("ebus_sdk.homie.MqttClient.from_config") as mock_from_config:
        mock_client = MagicMock()
        mock_client.sub_callbacks = {}
        mock_from_config.return_value = mock_client
        c = Controller(mqtt_cfg={"host": "localhost", "port": 1883})
        c.set_on_disconnect_callback(lambda clean: seen.append(clean))
        wired = mock_from_config.call_args.kwargs["on_disconnect_callback"]
    assert wired is not None
    # Registered at construction, reads the setter's value at call time.
    wired(0)
    wired(7)
    assert seen == [True, False]
