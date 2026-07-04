"""Home Assistant MQTT discovery support for eBus proxies.

`discovery` parses Home Assistant device-based discovery into a neutral
`HADevice` / `HAComponent` model; `semantics` derives eBus `PropertySpec`s from
the HA `device_class` / `unit_of_measurement` hints a component carries. This is
the shared HA -> eBus front door: subscribe a broker's `homeassistant/device/+`
discovery topics, parse them here, and emit the result as Homie 5 via the
observable-model pattern (see `doc/building-a-proxy.md` and
`doc/ha-mqtt-discovery.md`).

`emit` is the reverse front door (eBus/Homie -> HA): given a Homie 5
`$description` or a `DiscoveredDevice`, serialize it back into HA device-based
discovery config so an eBus device surfaces to Home Assistant. Parse and emit
reuse the same neutral model. See issue SDK-dn4.
"""

from .discovery import (
    HAAvailability,
    HAAvailabilitySource,
    HAComponent,
    HADevice,
    HAOrigin,
    HARemoval,
    ParseResult,
    extract_value_field,
    parse_device_config,
    read_field,
)
from .bridge import HaDiscoveryBridge
from .customize import ebus_default_override
from .emit import (
    OverrideHook,
    PropertyContext,
    config_topic,
    device_class_for,
    homie_description_to_ha,
    homie_device_to_ha,
    homie_property_to_component,
    platform_for,
    state_class_for,
    to_config,
)
from .provenance import (
    EBUS_IMPORTED_EXTENSION,
    EBUS_SDK_ORIGIN,
    EBUS_SDK_ORIGIN_NAME,
    HA_ECOSYSTEM,
    IMPORTED_FROM_ATTRIBUTE,
    imported_extension,
    imported_from_attribute,
    imported_source,
    is_ebus_sdk_origin,
    is_imported,
)
from .semantics import derive_spec, unit_for

__all__ = [
    "HAAvailability",
    "HAAvailabilitySource",
    "HAComponent",
    "HADevice",
    "HAOrigin",
    "HARemoval",
    "ParseResult",
    "extract_value_field",
    "parse_device_config",
    "read_field",
    "derive_spec",
    "unit_for",
    # Emit (Homie/eBus -> HA discovery)
    "HaDiscoveryBridge",
    "OverrideHook",
    "PropertyContext",
    "config_topic",
    "device_class_for",
    "ebus_default_override",
    "homie_description_to_ha",
    "homie_device_to_ha",
    "homie_property_to_component",
    "platform_for",
    "state_class_for",
    "to_config",
    # Loop-avoidance / provenance (HA <-> eBus round-trip guards)
    "EBUS_IMPORTED_EXTENSION",
    "EBUS_SDK_ORIGIN",
    "EBUS_SDK_ORIGIN_NAME",
    "HA_ECOSYSTEM",
    "IMPORTED_FROM_ATTRIBUTE",
    "imported_extension",
    "imported_from_attribute",
    "imported_source",
    "is_ebus_sdk_origin",
    "is_imported",
]
