# Bridging eBus devices to Home Assistant (HA MQTT discovery)

This guide covers the reverse of `doc/ha-mqtt-discovery.md`: taking Homie 5 / eBus devices that already exist on an MQTT broker and surfacing them to Home Assistant as MQTT-discovered entities. Where `ha-mqtt-discovery.md` describes parsing HA discovery **into** eBus, this describes emitting eBus **out** to HA. The forward parser and this emitter share one neutral model (`ebus_sdk.ha.HADevice` / `HAComponent`), so `parse_device_config(to_config(dev))` round-trips.

Everything here lives in `ebus_sdk.ha`. Read `doc/ha-mqtt-discovery.md` first for the HA discovery format itself; this guide assumes it.

## The shape of the problem

One or more Homie devices are published on MQTT. You want Home Assistant to show them as entities. A Homie controller-role app discovers the devices (SDK `Controller` / `DiscoveredDevice`) and publishes the corresponding Home Assistant MQTT discovery topics; HA then creates entities. The translation is clean because Homie gives every property its own topic whose payload IS the value, so there is no shared-JSON `value_template` gymnastics (the template is just `{{ value }}`).

Home Assistant's model: a DEVICE has one or more ENTITIES; in device-based discovery each entity is a COMPONENT (an entry in the `cmps` map of `homeassistant/device/<id>/config`). One Homie property maps to one HA entity/component. This is the exact inverse of what the forward `ekm-proxy` path does.

## Two layers: pure translation, and a runtime bridge

The design separates the pure, broker-free translation from the runtime that drives it, so you can unit-test the first without any broker.

- `emit` (translation): `homie_description_to_ha` / `homie_device_to_ha` build a neutral `HADevice`; `to_config` serializes it to the HA discovery payload. No MQTT.
- `bridge` (runtime): `HaDiscoveryBridge` wraps a `Controller` and owns only WHEN to (re)publish and WHERE.
- `customize` (optional quality): `ebus_default_override` sharpens metadata for eBus devices.
- `provenance` (loop avoidance): guards against a HA <-> eBus round-trip echo.

## Quick start

```python
from ebus_sdk import Controller
from ebus_sdk.ha import HaDiscoveryBridge

controller = Controller(mqtt_cfg=mqtt_cfg)   # discovery source
bridge = HaDiscoveryBridge(controller)       # wraps it
bridge.start()                               # wire callbacks + publish known devices
controller.start_discovery()                 # find devices; the bridge publishes each
```

As devices are discovered (and each time their `$description` changes), the bridge serializes them and publishes the retained config to `homeassistant/device/<object_id>/config`. When a device is removed, it clears that topic. That is the whole app.

## The translation, property by property

For each Homie property, generic inference produces one HA component:

- **platform**: `sensor` if read-only; a read-only boolean is a `binary_sensor`. If settable, by datatype: boolean -> `switch`, float/integer -> `number`, enum -> `select`, string -> `text`.
- **device_class + state_class**: inferred from the Homie `$unit` (the inverse of `semantics.unit_for`): `Wh` -> energy + total_increasing, `W` -> power, `A` -> current, `V` -> voltage, `Hz` -> frequency, `°C` -> temperature, and so on. A bare `%` is deliberately left unresolved (it could be battery, humidity, or power factor); an override decides.
- **unit_of_measurement**: the Homie `$unit`.
- **state_topic**: the property's own topic, `ebus/5/<dev>/<node>/<prop>`.
- **command_topic**: `<state_topic>/set` for settable properties.
- **unique_id**: `<device>_<node>_<property>`, stable across restarts.
- **value encoding**: Homie booleans are the strings `"true"` / `"false"`, emitted as HA `payload_on` / `payload_off` (and as the switch command payloads). Enums carry their Homie `format` as HA `options`. A `datetime` datatype gets `device_class: timestamp`.
- **availability**: device-level, derived once from the device `$state` topic. Homie has five states; HA availability is binary, so a template maps `ready` -> online and everything else -> offline.

The device block comes from the `$description` name plus the eBus `info` capability when its live values are available (vendor-name -> manufacturer, model, serial-number -> identifiers, firmware-version -> sw_version). Homie parent/child topology maps to HA `via_device`.

You can call the translation directly, without a bridge or broker:

```python
from ebus_sdk.ha import homie_description_to_ha, to_config

ha_device = homie_description_to_ha(description_dict, "panel-1")
config = to_config(ha_device)   # the dict you'd publish to the config topic
```

## Choosing which devices to bridge

`HaDiscoveryBridge` bridges every device it discovers by default. Two parameters narrow that, and they AND together:

- `include=[...]`: an allowlist of device ids.
- `device_filter=lambda dev: ...`: an arbitrary predicate.

```python
HaDiscoveryBridge(controller, include=["panel-1", "panel-2"])
HaDiscoveryBridge(controller, device_filter=lambda d: d.is_root)
```

## Per-device mapping (the two-tier override)

Generic inference works out of the box. An OPTIONAL per-property override hook covers what inference cannot nail: force a `device_class` (a unitless 0-100 that is really a battery percent), pick a platform, set icon / entity_category / friendly name, or SUPPRESS an entity by returning `None`. The override wins; inference fills the gaps. This mirrors the forward `declaration.resolve` two-tier shape, inverted.

A hook has the signature `(HAComponent, PropertyContext) -> HAComponent | None`:

```python
def force_battery(component, ctx):
    if ctx.node_id == "battery" and ctx.prop_id == "soc":
        component.device_class = "battery"
    if ctx.prop_id.startswith("_"):
        return None            # suppress internal properties
    return component
```

When a bridge discovers more than one device, it resolves WHICH hook to use per device, with this precedence:

1. `overrides={device_id: hook}` -> the per-device mapping, if present.
2. `override_for=lambda dev: hook_or_None` -> a resolver, consulted next.
3. `default_override=hook` -> the generic mapping used when neither of the above yields a hook.

A device with no specific mapping falls back to the generic one; a `default_override` of `None` means pure inference. So `overrides` wins over `override_for` wins over `default_override` wins over inference (which always runs first; a hook only adjusts or suppresses its result).

```python
HaDiscoveryBridge(
    controller,
    overrides={"panel-a": mapper_a},          # panel-a uses its own hook
    default_override=ebus_default_override,    # everyone else: eBus-aware generic
)
```

## The eBus-aware customizer

`ebus_default_override` is a ready-made hook that recognizes the eBus capability vocabulary (`energy.ebus.capability.<capability>` node types plus known property ids) and emits better metadata than unit inference alone: `meter/imported-energy` -> energy + total_increasing, `battery/soc` -> battery (resolving the ambiguous percent), `info/*` -> the `diagnostic` entity category. It only ADDS or SHARPENS; it never suppresses, so it is safe as a blanket `default_override`. Generic Homie devices that do not use the eBus vocabulary fall through to plain inference unchanged.

## Lifecycle and offline handling

- Device discovered, or `$description` changed: publish (retained) `homeassistant/device/<id>/config`.
- Device removed (empty retained `$state`): publish an empty retained payload to that config topic, so HA drops the device.
- Transient offline (`lost` / `disconnected`): handled by the availability template. The entity shows unavailable but is NOT removed. Set `clear_on_lost=True` if you would rather clear discovery on `lost` as well.
- `bridge.stop()` restores the Controller's prior callbacks; with `clear_on_stop=True` it also clears everything it published. The bridge is a context manager, so a `with HaDiscoveryBridge(controller, clear_on_stop=True) as bridge:` block guarantees `stop()` runs on exit.

`HaDiscoveryBridge` chains onto (does not clobber) any callbacks already registered on the Controller: its handler runs first, then the pre-existing one.

### Cleaning up orphaned configs

The `homeassistant/device/<id>/config` topic is owned by the bridge, not by the exported eBus device. It can be orphaned if the bridge is retired without clearing, or if an eBus device is removed while the bridge is down (nobody heard the removal). `reconcile()` closes this gap: it subscribes to `<prefix>/device/+/config`, identifies the retained configs THIS SDK emitted (by their `ebus-sdk` origin, so it never touches Home Assistant's own or another integration's), and clears any that no longer correspond to a present device. It runs automatically `reconcile_after` seconds after `start()` (default 5.0; set to 0/None to disable and call `reconcile()` yourself once discovery has warmed up).

Because availability is tied to the eBus device `$state` and Home Assistant reads values directly from the `ebus/5/...` topics, the bridge is out of the data path once a device is discovered: a dead bridge does not make live devices look dead, so orphaned-config cleanup is housekeeping, not a correctness concern.

## Loop avoidance: do not re-export what you imported

The first client of the forward direction was `ekm-proxy`, a HA -> eBus proxy: it consumes HA MQTT discovery and republishes the device as eBus/Homie. If a `HaDiscoveryBridge` then runs on the same broker, it would re-emit that mirror BACK to Home Assistant as a duplicate. Two layered guards prevent that echo.

**Guard A: origin self-echo.** Every config the emitter publishes carries a distinctive HA `origin` (`EBUS_SDK_ORIGIN`, name `ebus-sdk`). A HA -> eBus proxy calls `is_ebus_sdk_origin(parsed_device)` and ignores any HA discovery the SDK itself emitted, breaking the HA -> eBus -> HA cycle at the import boundary with no per-device cooperation.

**Guard B: the `imported` provenance marker.** A proxy that creates an eBus device FROM an external ecosystem marks it two ways: it advertises the `energy.ebus.imported` Homie extension, and it sets a device-level `imported-from` attribute in the `$description` naming the SOURCE (for example `"imported-from": "ha"`). `HaDiscoveryBridge` reads it and, by default (`skip_reimport=True`), does NOT re-export a device imported from Home Assistant. Recording the source lets the guard be precise rather than blunt: a device imported from Zigbee (`"imported-from": "zigbee"`) is still exported to HA, because that is not a round trip.

The `imported-from` attribute is spec-safe: the Homie 5 forward-compatibility rule requires controllers to ignore unknown `$description` fields but keep the device (convention.md §Forward compatibility). A reverse proxy stamps both pieces when it builds the device:

```python
from ebus_sdk import Device
from ebus_sdk.ha import imported_extension, imported_from_attribute, HA_ECOSYSTEM

Device(
    id="mirror-1",
    ...,
    extensions=[imported_extension()],                     # advertises the extension
    description_extras=imported_from_attribute(HA_ECOSYSTEM),  # {"imported-from": "ha"}
)
```

and readers use `is_imported(device)` / `imported_source(device)`.

Note on terminology: Homie itself uses "bridge" for a protocol-gateway device (a parent fronting child devices), which maps to our proxy/adapter ACTOR, not to a device's provenance. We use "imported" for the marker to name the DIRECTION it guards (do not re-export what was imported), keeping the actor ("bridge" / "proxy") and the resulting state ("imported") distinct. The `energy.ebus.imported` extension is provisional pending a formal Homie extension specification document (see `../convention/extensions/`).

Beyond the two guards, the operational separations still apply: run the two directions on different discovery prefixes or brokers, or scope the bridge with `include=[...]`.

## Verifying without Home Assistant

You do not need a running Home Assistant to be confident the bridge publishes the right thing.

- **Deterministic (no broker):** unit-test the translation directly. Feed a synthetic `$description` to `homie_description_to_ha`, assert the emitted `to_config(...)` dict. This is what `tests/test_ha_emit.py` does.
- **Live broker (no HA):** stand up a real broker (for example mosquitto), publish a synthetic Homie device, point a `Controller` + `HaDiscoveryBridge` at it, and subscribe to `homeassistant/device/+/config` with an independent client to capture and check the payloads. `examples/ha-discovery-bridge` does exactly this end to end. Home Assistant only adds the final "and HA renders entities" step, which cannot be automated here anyway.

## API summary

Translation: `homie_description_to_ha`, `homie_device_to_ha`, `to_config`, `config_topic`, `homie_property_to_component`, `device_class_for`, `state_class_for`, `platform_for`, `PropertyContext`, `OverrideHook`.

Runtime: `HaDiscoveryBridge`.

Customizer: `ebus_default_override`.

Loop avoidance: `EBUS_SDK_ORIGIN`, `is_ebus_sdk_origin`, `imported_extension`, `imported_from_attribute`, `is_imported`, `imported_source`, `EBUS_IMPORTED_EXTENSION`, `IMPORTED_FROM_ATTRIBUTE`, `HA_ECOSYSTEM`.
