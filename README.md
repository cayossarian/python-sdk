# ebus-sdk

[![PyPI](https://img.shields.io/pypi/v/ebus-sdk)](https://pypi.org/project/ebus-sdk/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Python SDK for the [Electrification Bus (eBus)](https://ebus.energy) integration framework, which adopts and supports the [Homie Convention](https://homieiot.github.io).

## Installation

```bash
pip install ebus-sdk
```

## Quick Start

### Device Role

Create a Homie device that publishes sensor data:

```python
from ebus_sdk import Device, Node, PropertyDatatype, Unit

# Create device
device = Device('my-device-id', name='My Sensor', mqtt_cfg={
    'host': 'mqtt.example.com',
    'port': 1883
})

# Add a node with properties
node = device.add_node_from_dict({
    'id': 'sensors',
    'name': 'Sensors',
    'type': 'sensor'
})

# Add a temperature property
temp = node.add_property_from_dict({
    'id': 'temperature',
    'name': 'Temperature',
    'datatype': PropertyDatatype.FLOAT,
    'unit': Unit.DEGREE_CELSIUS
})

# Start and publish
device.start_mqtt_client()
temp.set_value(23.5)
```

### Device Trees (parent / child)

Build a tree of devices that share a single MQTT connection. The root device owns the connection (and the Last Will), every child borrows it via the `parent=` constructor arg, and `$description` `root` / `parent` / `children` fields are kept in sync automatically. The tree can be any depth.

```python
panel = Device('panel-1', type='energy.ebus.device.electrical-panel', mqtt_cfg={...})
panel.start_mqtt_client()

# Add 32 circuit children inside one state transition — the broker sees
# exactly one INIT→READY cycle on the panel, not 32.
with panel.state_transition():
    for cid in commissioned_circuits:
        Device(id=cid, type='energy.ebus.device.circuit', parent=panel)

# Three-level tree: panel → BESS child → MID grandchild
bess = Device(id='bess-1', type='...battery-storage', parent=panel)
Device(id='mid-1', type='...metering', parent=bess)

# Remove a child at runtime (runs the Homie remove-child protocol)
panel.children()[0].delete()
```

Children may have children of their own. A single Last Will registered on the root marks the entire tree `lost` if the publisher process dies — controllers compute effective state per the Homie 5 precedence table (see [`HOMIE_EFFECTIVE_STATE_TABLE`](src/ebus_sdk/homie.py)).

### Controller Role

Discover and monitor Homie devices:

```python
from ebus_sdk import Controller, DiscoveredDevice

def on_device_discovered(device: DiscoveredDevice):
    print(f'Found: {device.device_id}')

def on_property_changed(device_id, node_id, prop_id, new_val, old_val):
    print(f'{device_id}/{node_id}/{prop_id} = {new_val}')

controller = Controller(mqtt_cfg={'host': 'mqtt.example.com', 'port': 1883})
controller.set_on_device_discovered_callback(on_device_discovered)
controller.set_on_property_changed_callback(on_property_changed)
controller.start_discovery()
```

Controllers can also navigate device hierarchies and compute effective state:

```python
# Walk the tree
roots = controller.get_root_devices()
for root in roots:
    for descendant in controller.get_descendants(root.device_id):
        # When the root is lost/disconnected/sleeping/init, every descendant
        # is effectively the same regardless of its own reported $state.
        print(f'{descendant.device_id}: {controller.get_effective_state(descendant.device_id)}')
```

Three controller discovery modes select what the controller listens for:

```python
# Wildcard (default) — every device on the broker
Controller(mqtt_cfg=cfg)

# Single-device — subscribe to exactly one device, no children, no wildcards
Controller(mqtt_cfg=cfg, device_id='panel-1')

# Tree-rooted — subscribe to a root and auto-subscribe to its descendants
# as they're announced; subscription changes are gated on the parent's
# $state init→ready edge per the Homie 5 spec.
Controller(mqtt_cfg=cfg, root_device_id='panel-1')
```

Tree-rooted mode is the right pick for consumers that want exactly one
device's tree on a multi-publisher broker — wildcard would re-introduce
multi-panel scope creep at the application layer, and single-device would
see the root and none of its children. As the publisher mutates the tree
(`Device(parent=...)` to add, `child.delete()` to remove), descendants are
subscribed or dropped on the parent's next init→ready transition.

## Module Structure

```
src/ebus_sdk/
├── __init__.py     # Package exports
├── homie.py        # Homie convention implementation (Device, Node, Property, Controller, ...)
└── property.py     # Application-level property abstractions
```

MQTT transport lives in the separate [`ebus-mqtt-client`](https://github.com/electrification-bus/ebus-mqtt-client) package; this SDK depends on it.

### homie.py

Core Homie convention implementation:

- **Device** - Represents a Homie device; pass `parent=` to build a child in a tree
- **Node** - Groups related properties within a device
- **Property** - Individual data points (sensors, controls)
- **Controller** - Discovers and monitors Homie devices on a broker; navigates trees and computes effective state
- **DiscoveredDevice** - Represents a device found by the controller; exposes `root_id`, `parent_id`, `children_ids`, `is_root`
- **DeviceState** - Enum: `init`, `ready`, `disconnected`, `sleeping`, `lost`
- **HOMIE_EFFECTIVE_STATE_TABLE** - Homie 5 state-precedence table used by `Controller.get_effective_state()`
- **PropertyDatatype** - Enum: `STRING`, `INTEGER`, `FLOAT`, `BOOLEAN`, `ENUM`, `COLOR`, `DATETIME`, `DURATION`, `JSON`
- **Unit** - Common units: `DEGREE_CELSIUS`, `PERCENT`, `WATT`, `KILOWATT_HOUR`, etc.

### property.py

Application-level property abstractions for bridging application state to Homie:

- **Property** - Thread-safe observable property with change callbacks
- **GroupedPropertyDict** - Two-level dictionary organizing properties by group
- **PropertyDict** - Simple property dictionary
- **ChangeEvent** - Enum for property change event types

## Examples

See [`examples/README.md`](examples/README.md) for example scripts demonstrating device and controller usage.

## Requirements

- Python 3.10+
- paho-mqtt >= 1.6.1

## Releases

See [CHANGELOG.md](CHANGELOG.md). 0.2.0 introduces parent/child device trees and contains breaking changes to the `Device` constructor — see the changelog entry before upgrading from 0.1.x.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to file Discussions, Issues, and pull requests. Pure MQTT-transport changes (TLS, auth, paho upgrades) belong in [`ebus-mqtt-client`](https://github.com/electrification-bus/ebus-mqtt-client), not here. Normative behavior tracks the [Electrification Bus specification](https://github.com/electrification-bus/specification).

## License

[MIT License](LICENSE) — Copyright (c) 2026 Clark Communications Corporation
