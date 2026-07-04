"""General Home Assistant -> eBus semantic derivation (the mapping fallback).

Given a Home Assistant sensor `device_class` and `unit_of_measurement`, derive
the eBus/Homie property datatype and unit (and any scale needed to reach the
eBus-canonical unit). This is the best-effort fallback used when a source field
has no explicit mapping entry: it lets any HA-discovered sensor be emitted onto
eBus using the semantics HA already carries, rather than being dropped.

Explicit, hand-authored mappings are always preferred; this only fills the gaps.
The returned `PropertySpec` is the general declaration type from
`ebus_sdk.declaration`; only the derivation here is Home-Assistant-specific.
"""

from __future__ import annotations

from typing import Optional

from ..declaration import PropertySpec
from ..homie import PropertyDatatype, Unit, sanitize_homie_id

# HA sensor device_class -> Homie datatype. Physical measurements are floats.
_DEVICE_CLASS_DATATYPE = {
    "energy": PropertyDatatype.FLOAT,
    "energy_storage": PropertyDatatype.FLOAT,
    "power": PropertyDatatype.FLOAT,
    "apparent_power": PropertyDatatype.FLOAT,
    "reactive_power": PropertyDatatype.FLOAT,
    "current": PropertyDatatype.FLOAT,
    "voltage": PropertyDatatype.FLOAT,
    "power_factor": PropertyDatatype.FLOAT,
    "frequency": PropertyDatatype.FLOAT,
    "temperature": PropertyDatatype.FLOAT,
    "duration": PropertyDatatype.FLOAT,
    "timestamp": PropertyDatatype.DATETIME,
}

# HA unit string -> (eBus Unit, scale to reach that eBus canonical unit). Units
# the eBus sdk does not yet define (e.g. VA apparent power, varh reactive energy)
# are absent here; such a field emits with unit=None until the sdk gains them.
_UNIT_TABLE = {
    "Wh": (Unit.WATT_HOUR, 1.0),
    "kWh": (Unit.WATT_HOUR, 1000.0),
    "MWh": (Unit.WATT_HOUR, 1_000_000.0),
    "W": (Unit.WATT, 1.0),
    "kW": (Unit.WATT, 1000.0),
    "A": (Unit.AMPERE, 1.0),
    "mA": (Unit.AMPERE, 0.001),
    "V": (Unit.VOLTS, 1.0),
    "kV": (Unit.VOLTS, 1000.0),
    "mV": (Unit.VOLTS, 0.001),
    "Hz": (Unit.HERTZ, 1.0),
    "°C": (Unit.DEGREE_CELSIUS, 1.0),
    "var": (Unit.VOLT_AMPERE_REACTIVE, 1.0),
    "kvar": (Unit.VOLT_AMPERE_REACTIVE, 1000.0),
    "%": (Unit.PERCENT, 1.0),
    "s": (Unit.SECONDS, 1.0),
    "min": (Unit.MINUTES, 1.0),
    "h": (Unit.HOURS, 1.0),
}


def unit_for(ha_unit: Optional[str]) -> tuple[Optional[Unit], float]:
    """Map a Home Assistant unit string to (eBus Unit, scale). Unknown -> (None, 1.0)."""
    if not ha_unit:
        return None, 1.0
    return _UNIT_TABLE.get(ha_unit, (None, 1.0))


def derive_spec(
    device_class: Optional[str],
    unit_of_measurement: Optional[str],
    field_name: Optional[str],
    default_capability: str = "meter",
) -> Optional[PropertySpec]:
    """Best-effort PropertySpec from HA discovery semantics.

    The property id is the sanitized source field name (Home Assistant does not
    give us an eBus property vocabulary, so we keep the source field name rather
    than invent a spec name). Returns None if no usable id can be formed.
    """
    prop_id = sanitize_homie_id(field_name or "")
    if not prop_id:
        return None
    unit, scale = unit_for(unit_of_measurement)
    datatype = _DEVICE_CLASS_DATATYPE.get(device_class)
    if datatype is None:
        datatype = PropertyDatatype.FLOAT if unit is not None else PropertyDatatype.STRING
    return PropertySpec(default_capability, prop_id, datatype, unit, scale)
