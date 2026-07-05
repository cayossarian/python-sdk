"""Declarative property specifications and a builder that materializes them.

A `PropertySpec` describes how a source field becomes an eBus property: which
capability (Homie node) it lives on, its Homie datatype and unit, an optional
unit `scale`, and whether it is settable. It is the declarative "schema" layer of
the proxy pattern (see `doc/building-a-proxy.md`). It is complementary to
`property.py`: a `PropertySpec` is a static declaration, while a `property.py`
`Property` is the live observable value built from it.

`build_from_declarations` turns a set of `PropertySpec`s into a live device in
one call: one Homie node per capability, an observable `Property` plus a Homie
property per spec, and the on-change binding between them, all inside a single
state transition. Acquisition code then only calls
`model.set_value(capability, prop_id, value)` and publishing follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Iterable, Optional

from .adapter import bind_property_to_homie
from .homie import Device, PropertyDatatype, Unit
from .property import GroupedPropertyDict
from .property import Property as ObservableProperty

_PYTHON_TYPE = {
    PropertyDatatype.FLOAT: float,
    PropertyDatatype.INTEGER: int,
    PropertyDatatype.BOOLEAN: bool,
    PropertyDatatype.STRING: str,
    PropertyDatatype.ENUM: str,
    PropertyDatatype.DATETIME: str,
    PropertyDatatype.DURATION: str,
    PropertyDatatype.JSON: "json",
}


def python_type_for(datatype: PropertyDatatype) -> Any:
    """The observable-`Property` python type for a Homie datatype (default: `str`)."""
    return _PYTHON_TYPE.get(datatype, str)


@dataclass(frozen=True)
class PropertySpec:
    """Declaration of one eBus property: where it lives and what it is.

    `capability` is the Homie node id (an eBus capability); `prop_id` is the
    Homie property id. `scale` multiplies a source value to reach `unit` (e.g.
    kWh -> Wh is 1000); it is metadata for a caller's mapping/resolver and is NOT
    applied by the builder. `python_type` overrides the observable-`Property`
    type (otherwise derived from `datatype`).

    `entity_setter` is the inbound-control translator for a settable property: a
    `callable(value)` invoked when a `/set` command arrives. When `settable=True`
    and `entity_setter` is given, `build_from_declarations` wires the whole
    inbound path automatically (see there); a settable spec without an
    `entity_setter` still gets a `/set` topic but no auto-wired handler.
    """

    capability: str
    prop_id: str
    datatype: PropertyDatatype
    unit: Optional[Unit] = None
    scale: float = 1.0
    settable: bool = False
    name: Optional[str] = None
    format: Optional[str] = None
    python_type: Any = None
    entity_setter: Optional[Callable] = None


def _default_node_type(capability: str) -> str:
    return f"energy.ebus.capability.{capability}"


def build_from_declarations(
    device: Device,
    model: GroupedPropertyDict,
    specs: Iterable[PropertySpec],
    *,
    node_type: Callable[[str], str] = _default_node_type,
    node_name: Callable[[str], str] = lambda capability: capability,
    values: Optional[dict] = None,
) -> dict:
    """Build bound Homie nodes/properties + observable properties from `specs`.

    Groups `specs` by capability (one Homie node each) and, for every spec,
    creates an observable `Property` in `model` and a Homie property on the node,
    wired together with `bind_property_to_homie` (the outbound/report path). Runs
    inside one `device.state_transition()`. `values` (a `{(capability, prop_id):
    value}` map) seeds initial values THROUGH the model after the structure is
    built, so they publish via the bindings. Returns
    `{(capability, prop_id): homie.Property}`.

    For a spec with `settable=True` AND an `entity_setter`, the inbound/control
    path is wired automatically: the observable `Property`'s `entity_setter` is
    registered on `model`, and the Homie property's `set_callback` is set to
    `partial(model.set_entity, capability, prop_id)`, so an arriving `/set`
    command routes `/set` payload -> `model.set_entity` -> the `entity_setter`.
    The `/set` subscription itself is already established when the property is
    added (`Node.add_property` -> `Property.set_subscribe`), so no `set_settable`
    toggle is needed.
    """
    grouped: dict[str, list[PropertySpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.capability, []).append(spec)

    homie_props: dict = {}
    with device.state_transition():
        for capability, cap_specs in grouped.items():
            node = device.add_node_from_dict(
                {"id": capability, "name": node_name(capability), "type": node_type(capability)}
            )
            if not model.has_group(capability):
                model.create_group(capability)
            for spec in cap_specs:
                py_type = spec.python_type if spec.python_type is not None else python_type_for(spec.datatype)
                model.add_property(capability, ObservableProperty(id=spec.prop_id, type=py_type))
                prop_dict: dict = {"id": spec.prop_id, "datatype": spec.datatype}
                if spec.name:
                    prop_dict["name"] = spec.name
                if spec.unit is not None:
                    prop_dict["unit"] = spec.unit
                if spec.settable:
                    prop_dict["settable"] = True
                if spec.format:
                    prop_dict["format"] = spec.format
                homie_prop = node.add_property_from_dict(prop_dict)
                bind_property_to_homie(model, capability, spec.prop_id, homie_prop)
                # Inbound/control path for a settable property with a translator:
                # /set payload -> model.set_entity -> entity_setter. The /set
                # subscription is already live from add_property -> set_subscribe.
                if spec.settable and spec.entity_setter is not None:
                    model.set_entity_setter(capability, spec.prop_id, spec.entity_setter)
                    homie_prop.set_set_callback(partial(model.set_entity, capability, spec.prop_id))
                homie_props[(capability, spec.prop_id)] = homie_prop

    if values:
        for (capability, prop_id), value in values.items():
            if (capability, prop_id) in homie_props:
                model.set_value(capability, prop_id, value)
    return homie_props


@dataclass(frozen=True)
class ResolvedProperty:
    """A `PropertySpec` paired with a resolved (already-scaled) value."""

    spec: PropertySpec
    value: Any


def resolve(
    field_names: Iterable[str],
    values: dict,
    mapping: dict,
    *,
    fallback: Optional[Callable[[str], Optional[PropertySpec]]] = None,
) -> list:
    """Resolve source fields to `PropertySpec`s and values: explicit mapping, then fallback.

    For each field name: look it up in `mapping` (a `{field: PropertySpec}` dict);
    if absent and `fallback` is given, call `fallback(field)` for a spec; if still
    unresolved, the field is held (skipped). The value from `values` is multiplied
    by the spec's `scale` (numeric, non-bool values only). Duplicate field names
    resolve once. Returns a list of `ResolvedProperty`.

    This is the two-tier mapping mechanism: a hand-authored `mapping` wins, and a
    generic `fallback` (e.g. `ebus_sdk.ha.derive_spec` over discovered components)
    fills the gaps. Feed the result to `build_from_declarations` via
    `specs_and_values`.
    """
    resolved: list = []
    seen: set = set()
    for field_name in field_names:
        if field_name in seen:
            continue
        seen.add(field_name)
        spec = mapping.get(field_name)
        if spec is None and fallback is not None:
            spec = fallback(field_name)
        if spec is None:
            continue
        value = values.get(field_name)
        if value is not None and spec.scale != 1.0 and isinstance(value, (int, float)) and not isinstance(value, bool):
            value = value * spec.scale
        resolved.append(ResolvedProperty(spec, value))
    return resolved


def specs_and_values(resolved: Iterable[ResolvedProperty]) -> tuple:
    """Split a `resolve` result into `(specs, values)` for `build_from_declarations`.

    `values` is `{(capability, prop_id): value}` and omits entries whose value is
    None (declared-but-not-yet-observed properties still appear in `specs`).
    """
    resolved = list(resolved)
    specs = [r.spec for r in resolved]
    values = {(r.spec.capability, r.spec.prop_id): r.value for r in resolved if r.value is not None}
    return specs, values
