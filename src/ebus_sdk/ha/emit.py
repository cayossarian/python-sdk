"""Home Assistant MQTT discovery emitter (the reverse of `discovery.py`).

Serializes a Homie 5 / eBus device into a Home Assistant *device-based*
discovery config: the JSON payload published (retained) to
`<prefix>/device/<object_id>/config`. This is the inverse front door to the
parser in `discovery.py`: a Homie controller discovers eBus devices on MQTT and
this module turns each one into the HA discovery message that surfaces it to
Home Assistant.

The translation is structurally clean because Homie gives every property its own
topic whose payload IS the value, so there is no shared-JSON `value_template`
gymnastics (the template is just `{{ value }}`). Per Homie property we emit one
HA component (entity) in the `cmps` map:

  - `platform`: `sensor` if read-only; if settable, by datatype
    (boolean->switch, float/integer->number, enum->select, string->text).
  - `device_class` + `state_class`: inferred from the Homie `$unit`
    (inverse of `semantics.unit_for`: Wh->energy+total_increasing, W->power, ...).
  - `unit_of_measurement`: the Homie `$unit`.
  - `state_topic`: the property's own `ebus/5/<dev>/<node>/<prop>` topic.
  - `command_topic`: `<state_topic>/set` for settable properties.
  - `unique_id`: `<device>_<node>_<property>` (stable across restarts).
  - device-level `availability`: derived from the device `$state` topic, mapping
    Homie's `ready` -> online and everything else -> offline via a template.

Two-tier design, symmetric to the forward `declaration.resolve`: generic
inference is the default and works out of the box; an OPTIONAL per-property
override hook covers what inference cannot nail (force a `device_class`, pick a
platform, set icon / entity_category / friendly-name, or SUPPRESS an entity by
returning `None`). The override wins; generic inference fills the gaps.

Parse (`discovery.py`) and emit (here) stay separate and reuse the same neutral
`HADevice` / `HAComponent` model, so `parse_device_config(to_config(dev))`
round-trips the shared fields. Spec reference: `doc/ha-mqtt-discovery.md`;
issue SDK-dn4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ..homie import (
    EBUS_HOMIE_DOMAIN,
    EBUS_HOMIE_VERSION_MAJOR,
    PropertyDatatype,
)
from .discovery import (
    HAAvailability,
    HAAvailabilitySource,
    HAComponent,
    HADevice,
    HAOrigin,
)
from .provenance import EBUS_SDK_ORIGIN, EBUS_SDK_ORIGIN_NAME

_DEFAULT_DISCOVERY_PREFIX = "homeassistant"
_DEFAULT_ORIGIN_NAME = EBUS_SDK_ORIGIN_NAME

# Inverse of `semantics._UNIT_TABLE`: a Homie/HA unit string -> the HA
# (device_class, default state_class) it implies. `%` is deliberately absent:
# a percent could be battery, humidity, or power_factor, so inference declines
# to guess and an override / customizer must decide. Apparent/reactive ENERGY
# (VAh / varh) are likewise absent: HA has no standard energy device_class for
# them, so they emit without one rather than misusing `energy` (which is real
# energy in Wh). Apparent/reactive POWER (VA / var) do have HA device classes.
_UNIT_SEMANTICS = {
    "Wh": ("energy", "total_increasing"),
    "kWh": ("energy", "total_increasing"),
    "MWh": ("energy", "total_increasing"),
    "W": ("power", "measurement"),
    "kW": ("power", "measurement"),
    "VA": ("apparent_power", "measurement"),
    "var": ("reactive_power", "measurement"),
    "kvar": ("reactive_power", "measurement"),
    "A": ("current", "measurement"),
    "mA": ("current", "measurement"),
    "V": ("voltage", "measurement"),
    "kV": ("voltage", "measurement"),
    "mV": ("voltage", "measurement"),
    "Hz": ("frequency", "measurement"),
    "°C": ("temperature", "measurement"),
    "°F": ("temperature", "measurement"),
    "K": ("temperature", "measurement"),
    "s": ("duration", "measurement"),
    "min": ("duration", "measurement"),
    "h": ("duration", "measurement"),
}

# Settable Homie datatype -> HA control platform (inverse of the sensor default).
_SETTABLE_PLATFORM = {
    PropertyDatatype.BOOLEAN: "switch",
    PropertyDatatype.FLOAT: "number",
    PropertyDatatype.INTEGER: "number",
    PropertyDatatype.ENUM: "select",
    PropertyDatatype.STRING: "text",
    PropertyDatatype.DATETIME: "text",
    PropertyDatatype.DURATION: "text",
}

# Homie booleans travel the wire as the strings "true"/"false"; HA needs to know
# both the reported payloads and (for switches) the command payloads.
_HOMIE_BOOL_ON = "true"
_HOMIE_BOOL_OFF = "false"

# Known eBus "info" capability property ids -> HADevice metadata field. Used to
# enrich the HA device block from an info node's live property values. This is a
# light, safe default; richer per-property mapping is the override hook's job.
_INFO_FIELD = {
    "vendor-name": "manufacturer",
    "vendor": "manufacturer",
    "manufacturer": "manufacturer",
    "model": "model",
    "model-id": "model_id",
    "serial-number": "serial_number",
    "firmware-version": "sw_version",
    "hardware-version": "hw_version",
}


def device_class_for(unit: Optional[str]) -> Optional[str]:
    """Infer an HA `device_class` from a Homie `$unit` string. Unknown -> None."""
    entry = _UNIT_SEMANTICS.get(unit) if unit else None
    return entry[0] if entry else None


def state_class_for(unit: Optional[str]) -> Optional[str]:
    """Infer an HA `state_class` from a Homie `$unit` string. Unknown -> None."""
    entry = _UNIT_SEMANTICS.get(unit) if unit else None
    return entry[1] if entry else None


def platform_for(datatype: Optional[str], settable: bool) -> str:
    """Infer the HA component platform from a Homie datatype + settable flag.

    Read-only booleans become `binary_sensor`; other read-only properties are
    `sensor`. Settable properties map to a control platform by datatype
    (boolean->switch, float/integer->number, enum->select, else text).
    """
    if settable:
        return _SETTABLE_PLATFORM.get(datatype, "text")
    if datatype == PropertyDatatype.BOOLEAN:
        return "binary_sensor"
    return "sensor"


def property_state_topic(device_id: str, node_id: str, prop_id: str) -> str:
    """The Homie topic a property publishes its value to."""
    return f"{EBUS_HOMIE_DOMAIN}/{EBUS_HOMIE_VERSION_MAJOR}/{device_id}/{node_id}/{prop_id}"


def device_state_topic(device_id: str) -> str:
    """The Homie `$state` topic for a device (drives HA availability)."""
    return f"{EBUS_HOMIE_DOMAIN}/{EBUS_HOMIE_VERSION_MAJOR}/{device_id}/$state"


@dataclass(frozen=True)
class PropertyContext:
    """Everything an override hook needs about one Homie property being emitted.

    Carries the identity (`device_id` / `node_id` / `prop_id`), the owning node's
    Homie `$type` (e.g. `energy.ebus.capability.meter`), and the raw property
    `$description` dict plus the fields already pulled out of it. The hook may
    read any of this to decide how to adjust (or suppress) the component.
    """

    device_id: str
    node_id: str
    node_type: Optional[str]
    prop_id: str
    prop: dict
    datatype: Optional[str]
    settable: bool
    unit: Optional[str]


# An override hook: given the generically-inferred component and its context,
# return a (possibly modified) component, or None to SUPPRESS the entity.
OverrideHook = Callable[[HAComponent, PropertyContext], Optional[HAComponent]]


def _availability_from_device_state(device_id: str) -> HAAvailability:
    """Map the Homie `$state` topic to a binary HA availability source.

    Homie has five states; HA availability is binary. `ready` -> online, every
    other state -> offline, expressed as a Jinja template over the `$state`
    payload so HA compares the rendered result to payload_available/not_available.
    """
    template = "{{ 'online' if value == 'ready' else 'offline' }}"
    src = HAAvailabilitySource(
        topic=device_state_topic(device_id),
        payload_available="online",
        payload_not_available="offline",
        value_template=template,
    )
    return HAAvailability(sources=[src], mode="latest")


def homie_property_to_component(
    device_id: str,
    node_id: str,
    prop_id: str,
    prop: dict,
    *,
    node_type: Optional[str] = None,
    override: Optional[OverrideHook] = None,
) -> Optional[HAComponent]:
    """Translate one Homie property `$description` into an `HAComponent`.

    `prop` is the property's Homie `$description` dict (name, datatype, unit,
    settable, format, ...). Generic inference builds the component; if `override`
    is given it is called last with the component and a `PropertyContext`, and
    may return a modified component or `None` to drop the entity.
    """
    datatype = prop.get("datatype")
    settable = bool(prop.get("settable", False))
    unit = prop.get("unit")
    name = prop.get("name")
    fmt = prop.get("format")

    platform = platform_for(datatype, settable)
    if datatype == PropertyDatatype.DATETIME:
        device_class = "timestamp"
        state_class = None
    else:
        device_class = device_class_for(unit)
        state_class = state_class_for(unit)

    # Emit-only extras that have no typed HAComponent field live in `config`.
    extras: dict = {}
    if settable:
        extras["command_topic"] = property_state_topic(device_id, node_id, prop_id) + "/set"
    if datatype == PropertyDatatype.BOOLEAN:
        extras["payload_on"] = _HOMIE_BOOL_ON
        extras["payload_off"] = _HOMIE_BOOL_OFF
    if datatype == PropertyDatatype.ENUM and fmt:
        options = [o.strip() for o in fmt.split(",") if o.strip()]
        if options:
            if platform == "select":
                extras["options"] = options
            else:
                # A read-only enum is an HA enum sensor carrying its options.
                device_class = device_class or "enum"
                extras["options"] = options

    component = HAComponent(
        key=f"{node_id}_{prop_id}",
        platform=platform,
        unique_id=f"{device_id}_{node_id}_{prop_id}",
        name=name,
        device_class=device_class,
        unit_of_measurement=unit,
        state_class=state_class,
        state_topic=property_state_topic(device_id, node_id, prop_id),
        value_template="{{ value }}",
        config=extras,
    )

    if override is not None:
        ctx = PropertyContext(
            device_id=device_id,
            node_id=node_id,
            node_type=node_type,
            prop_id=prop_id,
            prop=prop,
            datatype=datatype,
            settable=settable,
            unit=unit,
        )
        component = override(component, ctx)
    return component


def _device_block_from_info(
    device: HADevice,
    property_values: Optional[dict],
    description: dict,
) -> None:
    """Best-effort: enrich `device` metadata from an eBus `info` capability node.

    Looks for a node whose `$type` is an `energy.ebus.capability.info` (or a node
    literally named `info`) and copies known property VALUES (vendor-name, model,
    serial-number, firmware-version, ...) into the HA device block. Live values
    come from `property_values` ({node_id: {prop_id: value}}), which a
    description-only caller may not have; missing values are simply skipped.
    """
    if not property_values:
        return
    nodes = description.get("nodes", {}) if isinstance(description, dict) else {}
    for node_id, node in nodes.items():
        node_type = node.get("type", "") if isinstance(node, dict) else ""
        if node_id != "info" and not node_type.endswith(".info"):
            continue
        values = property_values.get(node_id, {})
        for prop_id, field_name in _INFO_FIELD.items():
            value = values.get(prop_id)
            if value in (None, ""):
                continue
            if field_name == "serial_number":
                device.serial_number = str(value)
                if str(value) not in device.identifiers:
                    device.identifiers.append(str(value))
            elif getattr(device, field_name, None) in (None, ""):
                setattr(device, field_name, str(value))


def homie_description_to_ha(
    description: dict,
    device_id: str,
    *,
    property_values: Optional[dict] = None,
    override: Optional[OverrideHook] = None,
    origin: Optional[HAOrigin] = None,
) -> HADevice:
    """Build a neutral `HADevice` from a Homie 5 `$description` dict.

    Walks the description's nodes/properties, turning each Homie property into an
    `HAComponent` (via generic inference plus the optional per-property
    `override` hook). `property_values` ({node_id: {prop_id: value}}) lets the
    eBus `info` capability enrich the HA device block; it is optional so this
    entry point is fully testable from a synthetic description alone.

    The result feeds `to_config` for the emitted discovery payload. This is the
    pure, broker-free core; `homie_device_to_ha` is the `DiscoveredDevice`
    convenience wrapper.
    """
    device = HADevice(
        identifiers=[device_id],
        name=description.get("name") if isinstance(description, dict) else None,
        origin=origin or EBUS_SDK_ORIGIN,
        availability=_availability_from_device_state(device_id),
    )
    parent = description.get("parent") if isinstance(description, dict) else None
    if parent:
        device.via_device = parent

    _device_block_from_info(device, property_values, description)

    nodes = description.get("nodes", {}) if isinstance(description, dict) else {}
    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        node_type = node.get("type")
        for prop_id, prop in node.get("properties", {}).items():
            if not isinstance(prop, dict):
                continue
            component = homie_property_to_component(
                device_id,
                node_id,
                prop_id,
                prop,
                node_type=node_type,
                override=override,
            )
            if component is not None:
                device.components[component.key] = component
    return device


def homie_device_to_ha(
    discovered,
    *,
    override: Optional[OverrideHook] = None,
    origin: Optional[HAOrigin] = None,
) -> Optional[HADevice]:
    """Build an `HADevice` from an SDK `DiscoveredDevice`.

    Convenience wrapper over `homie_description_to_ha`: pulls the parsed
    `$description` and the device's live property values (for `info`-capability
    enrichment) off the `DiscoveredDevice`. Returns `None` if the device has no
    description yet (nothing to translate).
    """
    description = getattr(discovered, "description", None)
    if not description:
        return None
    return homie_description_to_ha(
        description,
        discovered.device_id,
        property_values=getattr(discovered, "properties", None),
        override=override,
        origin=origin,
    )


def _availability_to_dict(av: HAAvailability) -> dict:
    """Serialize availability. Single source -> flat keys; multiple -> list form."""
    if len(av.sources) == 1:
        src = av.sources[0]
        out: dict = {
            "availability_topic": src.topic,
            "payload_available": src.payload_available,
            "payload_not_available": src.payload_not_available,
        }
        if src.value_template:
            out["availability_template"] = src.value_template
        return out
    sources = []
    for src in av.sources:
        item: dict = {"topic": src.topic}
        if src.payload_available != "online":
            item["payload_available"] = src.payload_available
        if src.payload_not_available != "offline":
            item["payload_not_available"] = src.payload_not_available
        if src.value_template:
            item["value_template"] = src.value_template
        sources.append(item)
    return {"availability": sources, "availability_mode": av.mode}


def _device_to_dict(device: HADevice) -> dict:
    """Serialize the HA `device` block, omitting empty fields."""
    d: dict = {}
    if device.identifiers:
        d["identifiers"] = list(device.identifiers)
    for field_name in (
        "name",
        "manufacturer",
        "model",
        "model_id",
        "sw_version",
        "hw_version",
        "serial_number",
    ):
        value = getattr(device, field_name, None)
        if value:
            d[field_name] = value
    if device.via_device:
        d["via_device"] = device.via_device
    return d


def _origin_to_dict(origin: HAOrigin) -> dict:
    d: dict = {"name": origin.name or _DEFAULT_ORIGIN_NAME}
    if origin.sw_version:
        d["sw_version"] = origin.sw_version
    if origin.support_url:
        d["support_url"] = origin.support_url
    return d


# HAComponent typed fields -> HA config key. Only non-None values are emitted.
_COMPONENT_FIELDS = (
    ("platform", "platform"),
    ("unique_id", "unique_id"),
    ("name", "name"),
    ("device_class", "device_class"),
    ("unit_of_measurement", "unit_of_measurement"),
    ("state_class", "state_class"),
    ("state_topic", "state_topic"),
    ("value_template", "value_template"),
)

# Structural keys never copied out of a component's free-form `config` overlay
# (they belong to the root payload, not a component entry).
_CONFIG_SKIP = {"platform", "device", "origin", "components", "~"}


def _component_to_dict(comp: HAComponent) -> dict:
    """Serialize one `HAComponent` to its `cmps` entry."""
    d: dict = {}
    for attr, key in _COMPONENT_FIELDS:
        value = getattr(comp, attr, None)
        if value is not None:
            d[key] = value
    # Overlay emit-only extras (command_topic, payload_on/off, options, icon,
    # entity_category, ...) and any parse-carried config, without clobbering the
    # typed fields above.
    for k, v in (comp.config or {}).items():
        if k in _CONFIG_SKIP:
            continue
        d.setdefault(k, v)
    return d


def to_config(device: HADevice) -> dict:
    """Serialize an `HADevice` into an HA device-based discovery payload.

    Returns the dict published (retained) to
    `<discovery_prefix>/device/<object_id>/config`. Device-level `availability`
    is emitted once at the root and shared by all components. Empty/absent
    fields are omitted. Use `config_topic` for the matching topic string.
    """
    payload: dict = {
        "device": _device_to_dict(device),
        "origin": _origin_to_dict(device.origin or HAOrigin(name=_DEFAULT_ORIGIN_NAME)),
    }
    if device.availability:
        payload.update(_availability_to_dict(device.availability))
    payload["components"] = {key: _component_to_dict(comp) for key, comp in device.components.items()}
    return payload


def config_topic(device: HADevice, *, discovery_prefix: str = _DEFAULT_DISCOVERY_PREFIX) -> str:
    """The `<prefix>/device/<object_id>/config` topic for a device.

    Uses the device's `primary_id` (serial number, else first identifier) as the
    object id, sanitized to the HA object-id character class `[a-zA-Z0-9_-]`.
    """
    object_id = device.primary_id or (device.name or "device")
    object_id = "".join(c if (c.isalnum() or c in "_-") else "_" for c in object_id)
    return f"{discovery_prefix}/device/{object_id}/config"
