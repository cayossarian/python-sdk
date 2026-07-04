"""Home Assistant MQTT discovery support for eBus proxies.

`discovery` parses Home Assistant device-based discovery into a neutral
`HADevice` / `HAComponent` model; `semantics` derives eBus `PropertySpec`s from
the HA `device_class` / `unit_of_measurement` hints a component carries. This is
the shared HA -> eBus front door: subscribe a broker's `homeassistant/device/+`
discovery topics, parse them here, and emit the result as Homie 5 via the
observable-model pattern (see `doc/building-a-proxy.md` and
`doc/ha-mqtt-discovery.md`).
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
]
