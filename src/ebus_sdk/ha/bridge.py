"""A Controller helper that bridges discovered eBus/Homie devices to Home Assistant.

`HaDiscoveryBridge` wraps an SDK `Controller` and turns the Homie devices it
discovers into Home Assistant MQTT discovery. The translation core lives in
`emit`; the bridge owns only the runtime lifecycle: WHEN to (re)publish and WHERE.

  - a device's `$description` arrives or changes -> serialize with `emit` and
    publish the config RETAINED to `<prefix>/device/<object_id>/config`, so
    Home Assistant creates/updates the entities.
  - a device is removed (empty retained `$state`) -> publish an empty retained
    payload to that same config topic, so Home Assistant drops the device.
  - transient offline (Homie `lost` / `disconnected`) is handled by the emitted
    availability template (the entity shows unavailable but is NOT removed),
    unless `clear_on_lost=True` is set to also clear discovery on `lost`.
  - orphaned configs THIS SDK published in a prior run for now-absent devices
    (e.g. a device removed while the bridge was down) are cleared by `reconcile`,
    which runs automatically shortly after `start()`. It only ever clears configs
    it recognizes as its own (by origin), never Home Assistant's or another
    integration's. The bridge is also a context manager, so a `with` block
    guarantees `stop()` (and `clear_on_stop`, if set) runs on exit.

Which devices, and which mapping per device:
  - `include` (allowlist of device ids) and `device_filter` (predicate) select
    which discovered devices to bridge; with neither set, every device is
    bridged.
  - per-device override hooks resolve with the precedence
    `overrides[device_id]` -> `override_for(device)` -> `default_override`; a
    device with no specific mapping falls back to the generic one (which may be
    `None`, meaning pure inference). This mirrors the forward
    `declaration.resolve` two-tier shape inverted.

The bridge chains onto (does not clobber) any callbacks already set on the
Controller: its handler runs first, then the pre-existing one. See issue
SDK-dn4 and `doc/building-a-proxy.md`.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Callable, Iterable, Optional

from ..homie import DeviceState, DiscoveredDevice
from .discovery import HAOrigin, parse_device_config
from .emit import OverrideHook, config_topic, homie_device_to_ha, to_config
from .provenance import HA_ECOSYSTEM, imported_source, is_ebus_sdk_origin, is_imported

logger = logging.getLogger("homie")

_DEFAULT_DISCOVERY_PREFIX = "homeassistant"


def _config_wildcard(discovery_prefix: str) -> str:
    """The subscription filter for all device-based discovery configs."""
    return f"{discovery_prefix}/device/+/config"


class HaDiscoveryBridge:
    """Publish HA MQTT discovery for the Homie devices a Controller discovers.

    Args:
        controller: an SDK `Controller` (the discovery source). Its `mqttc` is
            used to publish the HA discovery topics.
        discovery_prefix: HA's discovery prefix (default `homeassistant`).
        include: optional allowlist of device ids to bridge. `None` = no
            allowlist (subject only to `device_filter`).
        device_filter: optional predicate `(DiscoveredDevice) -> bool`; return
            False to skip a device. ANDed with `include`.
        overrides: optional `{device_id: OverrideHook}` map (per-device mapping).
        override_for: optional `(DiscoveredDevice) -> OverrideHook | None`
            resolver, consulted when `overrides` has no entry.
        default_override: the generic mapping used when neither `overrides` nor
            `override_for` yields a hook. `None` means pure inference.
        origin: optional HA `origin` block stamped on every emitted config.
        skip_reimport: if True (default), do NOT export a device that was
            imported FROM Home Assistant (marked with the `energy.ebus.imported`
            extension and `imported-from: ha`, or an unspecified source), avoiding
            a HA<->eBus round-trip echo. Devices imported from other ecosystems
            (`imported-from: zigbee`, ...) are still exported.
        reconcile_after: seconds after `start()` to auto-run `reconcile()`, which
            clears orphaned retained configs THIS SDK previously published for
            devices that are no longer present (e.g. removed while the bridge was
            down). Set to 0/None to disable the automatic pass (call `reconcile()`
            yourself once discovery has warmed up). Default 5.0.
        clear_on_lost: if True, also clear the discovery config when a device
            enters the Homie `lost` state (default False: rely on availability).
        clear_on_stop: if True, `stop()` clears all discovery configs this bridge
            published (default False: leave retained configs in place).
    """

    def __init__(
        self,
        controller,
        *,
        discovery_prefix: str = _DEFAULT_DISCOVERY_PREFIX,
        include: Optional[Iterable[str]] = None,
        device_filter: Optional[Callable[[DiscoveredDevice], bool]] = None,
        overrides: Optional[dict] = None,
        override_for: Optional[Callable[[DiscoveredDevice], Optional[OverrideHook]]] = None,
        default_override: Optional[OverrideHook] = None,
        origin: Optional[HAOrigin] = None,
        skip_reimport: bool = True,
        reconcile_after: Optional[float] = 5.0,
        clear_on_lost: bool = False,
        clear_on_stop: bool = False,
    ):
        self.controller = controller
        self.discovery_prefix = discovery_prefix
        self._include = set(include) if include is not None else None
        self._device_filter = device_filter
        self._overrides = dict(overrides) if overrides else {}
        self._override_for = override_for
        self._default_override = default_override
        self._origin = origin
        self._skip_reimport = skip_reimport
        self._reconcile_after = reconcile_after
        self._clear_on_lost = clear_on_lost
        self._clear_on_stop = clear_on_stop

        # device_id -> the config topic we last published (so we can clear it
        # even after the DiscoveredDevice is gone).
        self._published: dict = {}
        # config topic -> True for every SDK-emitted retained config we observe
        # on the broker (ours by origin), used by reconcile() to find orphans.
        self._observed_configs: dict = {}
        self._reconcile_timer: Optional[threading.Timer] = None
        self._started = False
        # Controller callbacks we displaced, restored on stop().
        self._prev_description = None
        self._prev_state_changed = None
        self._prev_removed = None

    def __enter__(self) -> "HaDiscoveryBridge":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Wire the bridge onto the Controller and publish already-known devices.

        Idempotent: calling `start()` twice is a no-op after the first. Chains
        onto any callbacks already registered on the Controller. Devices that
        already have a `$description` (e.g. retained, delivered before `start()`)
        are published immediately. Also subscribes to the HA discovery configs to
        find orphans (see `reconcile`), scheduling an automatic `reconcile()`
        after `reconcile_after` seconds unless that is 0/None.
        """
        if self._started:
            return
        self._started = True

        self._prev_description = self.controller._on_description_received
        self._prev_state_changed = self.controller._on_device_state_changed
        self._prev_removed = self.controller._on_device_removed

        self.controller.set_on_description_received_callback(self._chain(self._prev_description, self._on_description))
        self.controller.set_on_device_state_changed_callback(
            self._chain(self._prev_state_changed, self._on_state_changed)
        )
        self.controller.set_on_device_removed_callback(self._chain(self._prev_removed, self._on_removed))

        # Observe existing discovery configs so reconcile() can spot orphans we
        # (this SDK) published in a prior run for now-absent devices.
        mqttc = getattr(self.controller, "mqttc", None)
        if mqttc is not None:
            mqttc.subscribe(
                _config_wildcard(self.discovery_prefix), param=self._on_config_seen, qos=self.controller.qos
            )

        for device in list(self.controller.get_all_devices().values()):
            if device.description:
                self._publish_device(device)

        if self._reconcile_after:
            self._reconcile_timer = threading.Timer(self._reconcile_after, self.reconcile)
            self._reconcile_timer.daemon = True
            self._reconcile_timer.start()

    def stop(self) -> None:
        """Unwire the bridge, restoring the Controller's prior callbacks.

        If `clear_on_stop` was set, first clears every discovery config this
        bridge published (empty retained payload). Cancels a pending reconcile
        timer and unsubscribes the discovery-config observer. Idempotent.
        """
        if not self._started:
            return
        if self._reconcile_timer is not None:
            self._reconcile_timer.cancel()
            self._reconcile_timer = None
        mqttc = getattr(self.controller, "mqttc", None)
        if mqttc is not None and hasattr(mqttc, "unsubscribe"):
            mqttc.unsubscribe(_config_wildcard(self.discovery_prefix))
        if self._clear_on_stop:
            for device_id in list(self._published):
                self._clear_device(device_id)
        self.controller.set_on_description_received_callback(self._prev_description)
        self.controller.set_on_device_state_changed_callback(self._prev_state_changed)
        self.controller.set_on_device_removed_callback(self._prev_removed)
        self._started = False

    # -- selection + mapping resolution --------------------------------------

    def _should_bridge(self, device: DiscoveredDevice) -> bool:
        """True iff `device` passes the include allowlist, filter, and loop guard."""
        if self._skip_reimport and is_imported(device) and imported_source(device) in (None, HA_ECOSYSTEM):
            # A device a reverse proxy imported FROM Home Assistant (or from an
            # unspecified source); re-exporting it would echo the round trip.
            # Imports from other ecosystems are still exported (Guard B, SDK-dn4).
            return False
        if self._include is not None and device.device_id not in self._include:
            return False
        if self._device_filter is not None and not self._device_filter(device):
            return False
        return True

    def _hook_for(self, device: DiscoveredDevice) -> Optional[OverrideHook]:
        """Resolve the per-property override hook for `device`.

        Precedence: `overrides[device_id]` -> `override_for(device)` ->
        `default_override`. Returns `None` for pure inference.
        """
        if device.device_id in self._overrides:
            return self._overrides[device.device_id]
        if self._override_for is not None:
            hook = self._override_for(device)
            if hook is not None:
                return hook
        return self._default_override

    # -- publish / clear -----------------------------------------------------

    def _publish_device(self, device: DiscoveredDevice) -> None:
        """Serialize and publish (retained) the HA discovery config for `device`."""
        if not self._should_bridge(device):
            return
        ha_device = homie_device_to_ha(device, override=self._hook_for(device), origin=self._origin)
        if ha_device is None:
            return
        topic = config_topic(ha_device, discovery_prefix=self.discovery_prefix)
        payload = json.dumps(to_config(ha_device), ensure_ascii=False)
        if not self._publish(topic, payload, retain=True):
            return
        self._published[device.device_id] = topic
        logger.info(f"reason=haDiscoveryPublished,deviceID={device.device_id},topic={topic}")

    def _clear_device(self, device_id: str) -> None:
        """Publish an empty retained payload to the device's config topic (removal)."""
        topic = self._published.pop(device_id, None)
        if topic is None:
            return
        self._publish(topic, "", retain=True)
        logger.info(f"reason=haDiscoveryCleared,deviceID={device_id},topic={topic}")

    def _publish(self, topic: str, payload: str, *, retain: bool) -> bool:
        """Publish via the Controller's MQTT client. Returns False if unavailable."""
        mqttc = getattr(self.controller, "mqttc", None)
        if mqttc is None:
            logger.warning(f"reason=haDiscoveryNoMqttClient,topic={topic}")
            return False
        mqttc.publish(topic, payload, qos=self.controller.qos, retain=retain)
        return True

    # -- reconciliation (orphan cleanup) -------------------------------------

    def _on_config_seen(self, topic: str, payload) -> None:
        """Track retained discovery configs THIS SDK emitted (ours by origin).

        Fires for every message on `<prefix>/device/+/config`. We record only
        configs carrying `EBUS_SDK_ORIGIN` so `reconcile()` never touches configs
        owned by Home Assistant itself or other integrations. An empty payload
        (a cleared topic) drops the entry.
        """
        text = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else (payload or "")
        if text.strip() == "":
            self._observed_configs.pop(topic, None)
            return
        try:
            parsed = parse_device_config(text)
        except Exception:
            return
        if is_ebus_sdk_origin(parsed):
            self._observed_configs[topic] = True

    def _live_config_topics(self) -> set:
        """The config topics for devices we are currently bridging.

        Everything we have published, plus the expected topic for any discovered
        device that is bridgeable and has a description (covers devices announced
        but not yet published when reconcile runs).
        """
        live = set(self._published.values())
        for device in list(self.controller.get_all_devices().values()):
            if not device.description or not self._should_bridge(device):
                continue
            ha_device = homie_device_to_ha(device, override=self._hook_for(device), origin=self._origin)
            if ha_device is not None:
                live.add(config_topic(ha_device, discovery_prefix=self.discovery_prefix))
        return live

    def reconcile(self) -> int:
        """Clear orphaned discovery configs THIS SDK published for absent devices.

        Compares the SDK-emitted retained configs seen on the broker (identified
        by origin, so only our own) against the configs for currently-bridged
        devices, and clears any that no longer correspond to a present device
        (e.g. a device removed while the bridge was down). Returns the number of
        orphans cleared. Runs automatically `reconcile_after` seconds after
        `start()`; safe to call again once discovery has warmed up.
        """
        live = self._live_config_topics()
        orphans = [t for t in list(self._observed_configs) if t not in live]
        for topic in orphans:
            self._publish(topic, "", retain=True)
            self._observed_configs.pop(topic, None)
            logger.info(f"reason=haDiscoveryOrphanCleared,topic={topic}")
        return len(orphans)

    # -- Controller callback handlers ----------------------------------------

    def _on_description(self, device: DiscoveredDevice) -> None:
        self._publish_device(device)

    def _on_state_changed(self, device: DiscoveredDevice, old_state: str, new_state: str) -> None:
        if new_state == DeviceState.LOST.value and self._clear_on_lost:
            self._clear_device(device.device_id)
        elif new_state == DeviceState.READY.value and device.device_id not in self._published and device.description:
            # Recovered (or first ready after a clear): (re)publish.
            self._publish_device(device)

    def _on_removed(self, device: DiscoveredDevice) -> None:
        self._clear_device(device.device_id)

    @staticmethod
    def _chain(existing: Optional[Callable], ours: Callable) -> Callable:
        """Return a callback that runs `ours` then any pre-existing `existing`."""
        if existing is None:
            return ours

        def chained(*args):
            ours(*args)
            existing(*args)

        return chained
