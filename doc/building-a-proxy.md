# Building a proxy or adapter with ebus-sdk

This is the guide for publishing a device (or several) onto eBus / Homie 5 when its state changes over time: a proxy for a non-eBus-native device, an adapter for a local device, or a gateway/bridge. It is the pattern the SDK is built around, and it is what every mature eBus proxy uses. If you are writing a proxy and this pattern was not obvious, this document is the fix: read it first, before you reach for `homie.Device`.

## TL;DR

Keep a device's live state in an **observable model** (`GroupedPropertyDict` of observable `Property` objects), and register **per-property on-change callbacks** that mirror each change onto a Homie `Device` / `Node` / `Property` tree. Your acquisition code only ever updates the model; publishing to MQTT is an automatic side-effect. Do not drive `homie.Device` directly from your incoming data.

```python
from functools import partial
from ebus_sdk import (
    Device, PropertyDatatype, Unit,
    GroupedPropertyDict, ObservableProperty,
    set_homie_property_from_python_property, bind_property_to_homie,
)

# 1. Observable model (homie-agnostic)
model = GroupedPropertyDict()
model.add_property("meter", ObservableProperty(id="active-power", type=float))

# 2. Homie device + property
device = Device("my-meter", type="energy.ebus.device.submeter", mqtt_cfg=cfg)
device.start_mqtt_client()
with device.state_transition():
    node = device.add_node_from_dict({"id": "meter", "type": "energy.ebus.capability.meter"})
    homie_prop = node.add_property_from_dict(
        {"id": "active-power", "datatype": PropertyDatatype.FLOAT, "unit": Unit.WATT}
    )

# 3. Bind: model change -> Homie publish
bind_property_to_homie(model, "meter", "active-power", homie_prop)

# 4. Your acquisition code just updates the model; MQTT follows automatically
model.set_value("meter", "active-power", 1850.0)
```

## When to use this pattern (and when not to)

Use the observable-model pattern when your publisher has **evolving state**: values that update over time, multiple properties, multiple proxied devices, or settable/controllable properties. That is nearly every proxy and adapter.

You can skip it for the trivial case: publishing a handful of static values once. There, the plain `Device` / `Node` / `Property` API from the [README Quick Start](../README.md#quick-start) is enough. The moment you find yourself repeatedly pushing new values, reach for the model.

## The three layers

A proxy built this way has three clean layers. Keep them separate.

1. **Declarative definitions (the schema).** A plain data table describing each capability node and its properties: id, datatype, unit, settable, and so on. This is the single source of truth for both the observable model and the Homie tree. The SDK does not prescribe a specific structure; you define your own (dataclasses are common). See [Declarative definitions](#declarative-definitions-in-practice).
2. **The observable model (`GroupedPropertyDict`).** Homie-agnostic. Holds the device's live values as observable `Property` objects grouped by capability (one group per Homie node, conventionally). Your acquisition code calls `model.set_value(group, property_id, value)` and nothing else. It knows nothing about MQTT.
3. **The adapter.** Builds the Homie `Device` / `Node` / `Property` tree from the declarations, and wires each observable property to its Homie twin with an on-change callback. This is the only layer that touches both the model and Homie.

## Data flow

```
acquisition code
      │  model.set_value(group, id, value)
      ▼
GroupedPropertyDict  ──fires──►  PROPERTY_CHANGED
      │
      ▼  on-change callback (set_homie_property_from_python_property)
homie.Property.set_value(...)  ──►  MQTT (ebus/5/<device>/<node>/<property>)
```

`GroupedPropertyDict.set_value` fires the change event (and the callback) only when the value actually changes, so re-writing the same value does not republish. That dedup is free.

## The exported helpers

Two functions are exported from `ebus_sdk` so you never hand-roll the mirror:

- `set_homie_property_from_python_property(homie_property, python_property)`: the on-change adapter. It copies the observable property's current value onto its Homie twin. Register it as a `GroupedPropertyDict` on-change callback via `partial(...)`.
- `bind_property_to_homie(properties, group, property_id, homie_property)`: a one-call convenience that does the `add_property_on_change_callback(..., partial(set_homie_property_from_python_property, homie_property))` for you. Prefer this.

## Declarative definitions in practice

Drive the model and the Homie tree from one table so they cannot drift. A common shape is a pair of frozen dataclasses:

```python
from dataclasses import dataclass, field
from typing import Any, List, Optional
from ebus_sdk import PropertyDatatype, Unit

@dataclass(frozen=True)
class PropDecl:
    id: str
    python_type: Any                  # float, int, str, bool, "json"
    datatype: PropertyDatatype
    unit: Optional[Unit] = None
    settable: bool = False
    entity_setter_attr: Optional[str] = None  # method name for inbound /set (see below)

@dataclass(frozen=True)
class NodeDecl:
    group: str                        # GroupedPropertyDict group == Homie node id
    type: str                         # e.g. "energy.ebus.capability.meter"
    name: str
    props: List[PropDecl] = field(default_factory=list)

METER = NodeDecl("meter", "energy.ebus.capability.meter", "Meter", [
    PropDecl("active-power", float, PropertyDatatype.FLOAT, Unit.WATT),
    PropDecl("imported-energy", float, PropertyDatatype.FLOAT, Unit.WATT_HOUR),
])
```

A single build loop then materializes both representations from each declaration: create an observable `Property` in the `GroupedPropertyDict`, create the Homie property with the same metadata, and bind them. Because both sides come from one table, adding a property is a one-line change.

## Static vs dynamic device shape

The declarations above are static: the property set is known up front (a water heater always has the same shape). Many proxies are dynamic instead: the property set is discovered at runtime (per-meter fields from a discovery message, per-circuit properties on a panel, one child device per thing found). Both fit this pattern.

- **Static shape:** declare all properties once at construction, inside a single `state_transition()`.
- **Dynamic shape:** declare properties (and create child devices) as you discover them. Wrap structural changes in `state_transition()` so each batch is one `init` to `ready` cycle, or use `GroupedPropertyDict.bulk_update()` and observe `ChangeEvent.GROUP_CREATED` / `PROPERTY_ADDED` to mirror new structure onto Homie as it appears. The declaration table still drives what each discovered field becomes; you just apply it lazily.

## Device topology: bridge root plus proxied children

A proxy is not one flat device. Per the eBus [`proxy.md`](https://github.com/electrification-bus/specification/blob/main/data-models/proxy.md) convention:

- Publish a **bridge root device** of type `energy.ebus.device.bridge`. It owns the MQTT connection (and the Last Will) and carries an `info` capability whose `vendor-name` identifies the proxy publisher. It does not publish the proxied device's measurements itself.
- Publish **one child device per proxied device**, each `Device(id=..., type=..., parent=root)`. The proxied measurements live here.
- Name each child `{proxier-id}-{proxied-id}` (the proxied id is the device's stable serial when it has one). Consumers correlate a proxy and a native publisher of the same physical device by `info/serial-number`, not by device id.

Children share the root's single MQTT connection automatically (that is what `parent=` does), and one Last Will on the root marks the whole tree `lost` if the process dies. See [Device Trees](../README.md#device-trees-parent--child) in the README.

## Lifecycle and state

- **Batch structural changes.** Adding N nodes/properties inside one `with device.state_transition():` collapses to a single `$description` publish and one `init` to `ready` edge, instead of N. Always build a device's structure inside a transition.
- **Connect before you publish.** `Device(..., mqtt_cfg=...)` connects asynchronously. If you build and publish before the broker connection is established, the first retained `$description` / `$state` the broker keeps can be a pre-connect snapshot until the SDK's on-connect refresh corrects it. Wait for `device.mqttc.is_connected()` before the initial build so the first retained state is correct.
- **Drive `$state` from availability.** When your upstream reports a device offline, set the child `DeviceState.LOST` (and `READY` when it returns). The root's Last Will covers process death.

## Settable / bidirectional properties (control back to the device)

If eBus controllers should be able to command the proxied device (a relay, a setpoint, a DR event), wire the inbound path too:

1. Mark the declaration `settable=True` and name an `entity_setter` method that translates an incoming value into a device command.
2. Give the **observable** `Property` that `entity_setter` (via `Property(..., entity_setter=...)` or `add_property_from_dict({"entity_setter": ...})`).
3. Give the **Homie** property a `set_callback` that routes an inbound `/set` into the model: `set_callback = partial(properties.set_entity, group, property_id)` (or `set_property_entity`, depending on your model wrapper).

Inbound flow: MQTT `/set` -> Homie property `set_callback` -> `properties.set_entity(group, id, value)` -> your `entity_setter` -> device command. The outbound (report) path is unchanged: the device's real state updates the model, which mirrors back to Homie.

## The anti-pattern (what not to do)

Do not drive `homie.Device` / `Node` / `Property` directly from your acquisition loop and cache raw Homie property handles:

```python
# ANTI-PATTERN: no observable model, raw Homie handles kept in a dict
self._props = {}                     # (node, prop) -> homie.Property
...
self._props[("meter", "active-power")].set_value(read_watts())   # from the read loop
```

It works, and it is tempting because it is fewer lines at first. But it reinvents, more crudely, what `GroupedPropertyDict` already gives you: it has no queryable local model of the device, no change dedup, no clean seam for settable properties, and it diverges from every other eBus proxy so it reads differently for the next maintainer. If you find yourself storing a `dict` of `homie.Property` handles and calling `.set_value()` on them from your data path, switch to the observable model: put the values in a `GroupedPropertyDict` and `bind_property_to_homie` them.

## Checklist

- [ ] Declarative property/node definitions are the single source of truth.
- [ ] A `GroupedPropertyDict` holds live state; acquisition code only calls `set_value`.
- [ ] The Homie tree is built from the declarations inside a `state_transition()`.
- [ ] Each property is bound with `bind_property_to_homie` (never a hand-rolled mirror).
- [ ] A bridge root (`energy.ebus.device.bridge`) plus child devices named `{proxier-id}-{proxied-id}`.
- [ ] Structural changes are batched; the device is connected before the first publish.
- [ ] Settable properties (if any) are wired via `entity_setter` + `set_callback`.
- [ ] No raw `homie.Property` handles cached in your data path.

## Worked examples

- [`examples/utility-meter`](../examples/utility-meter) is the fullest reference: an observable `UtilityMeter` model plus a `UtilityMeterAdapter` that mirrors it onto Homie, including a settable capability.
- [`examples/simple-device`](../examples/simple-device) is a minimal version of the same shape.

## Related

- [README Quick Start](../README.md#quick-start): the plain `Device` API for static publishing.
- [`property.py`](../src/ebus_sdk/property.py): the observable `Property` / `GroupedPropertyDict` / `ChangeEvent` classes.
- [`adapter.py`](../src/ebus_sdk/adapter.py): the exported mirror helpers.
- eBus [`proxy.md`](https://github.com/electrification-bus/specification/blob/main/data-models/proxy.md): the normative proxier / device-id convention.
