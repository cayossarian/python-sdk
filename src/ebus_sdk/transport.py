"""Structural type for the MQTT transport the SDK is given.

Its own module because it is public API rather than an internal detail: a consumer who
cannot name the type gains nothing from the widening, so it is re-exported from ``ebus_sdk``
beside ``MqttClient``. Keeping a small public type out of a ~2,900-line module is the only
reason it is not in ``homie.py``; nothing else imports it today.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MqttTransport(Protocol):
    """What the SDK calls on a *caller-supplied* MQTT client.

    Three members, deliberately — not the full ``MqttClient`` surface.

    An injected client is never started or stopped by the SDK, and that is an invariant the
    code enforces rather than a convention:

    * ``Controller._connect_broker`` returns immediately when ``self.mqttc`` is already set,
      so the ``start()`` beside ``MqttClient.from_config(...)`` is unreachable for an
      injected client.
    * ``Controller.stop`` calls ``stop()`` only behind ``if self._owns_client``, which is
      ``mqttc is None`` fixed at construction.

    ``is_connected``, ``is_running`` and ``publish_and_flush`` are likewise absent because
    nothing on the ``Controller`` path calls them — they belong to the ``Device`` /
    ``Property`` path, which has no injection point.

    The narrowness is deliberate rather than incidental. Widening this to the full client
    surface would type the injection point as *something the SDK may start and stop* — the
    opposite of the ownership guarantee above — and would oblige every consumer to implement
    two lifecycle methods the SDK provably never calls on their object; for a host supplying
    a connection whose lifecycle it already manages elsewhere, those stubs are pure ceremony.

    If ``Device`` gains an injection point later it needs a wider contract than this (it does
    call ``is_connected``, ``is_running`` and ``publish_and_flush``) but still not
    ``start``/``stop``. That would be a second protocol deriving from this one rather than an
    edit to this one.

    Signatures mirror ``ebus_mqtt_client.MqttClient`` exactly, including the ``Any`` on
    ``subscribe``'s callback, so that ``MqttClient`` satisfies this unchanged. Returns are
    ``object`` because every call site in the SDK discards them.
    """

    def publish(self, topic: str, data: str, qos: int = 1, retain: bool = False) -> object: ...

    def subscribe(self, sub: str, param: Any, qos: int = 1) -> object: ...

    def unsubscribe(self, sub: str) -> object: ...
