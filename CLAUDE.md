# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python SDK (`ebus-sdk`) for the Electrification Bus (eBus) integration framework, implementing the Homie MQTT Convention (version 5). The SDK provides:
- **Device role**: Framework for representing devices, nodes, and properties over MQTT
- **Controller role**: Auto-discovery and interaction with Homie devices on an MQTT broker

Links:
- eBus: https://ebus.energy
- Homie Convention: https://homieiot.github.io

## Installation

```bash
pip install -e .  # Development (editable)
pip install .     # Local install
```

## Core Architecture

### Three-Layer Property System

The codebase implements a sophisticated three-layer property abstraction:

1. **PythonProperty** (`src/ebus_sdk/property.py`): A lightweight property wrapper with callbacks
   - Thread-safe property value storage with change detection
   - Supports on_change and on_set callbacks
   - Can have an optional `entity_setter` for bidirectional control

2. **GroupedPropertyDict** (`src/ebus_sdk/property.py`): Collection manager for organizing properties
   - Two-level dictionary: `groups -> property_id -> PythonProperty`
   - Thread-safe operations with observer pattern for bulk updates
   - Use `bulk_update()` context manager for efficient batch operations

3. **Homie Property** (`src/ebus_sdk/homie.py`): MQTT-enabled properties following Homie convention
   - Maps to `ebus/5/{device_id}/{node_id}/{property_id}` topics
   - Supports settable properties via `/set` topics
   - Handles retained messages and QoS levels

### Device Hierarchy

```
Device (src/ebus_sdk/homie.py)
├── MQTT connection (MqttClient from src/ebus_sdk/mqtt.py)
├── State management (DeviceState: init, ready, disconnected, sleeping, lost)
├── $description attribute (JSON schema published on state transitions)
└── Nodes
    └── Properties
        ├── Value publication to MQTT
        └── Subscription to /set topics (if settable)
```

### Adapter Pattern

The `examples/simple-device` demonstrates the recommended adapter pattern:
- **ExampleDevice**: Application-level device using PythonProperty/GroupedPropertyDict
- **ExampleDeviceAdapter**: Bridges application properties to Homie/MQTT
- Connect changes via `add_property_on_change_callback()` with `partial(set_homie_property_from_python_property, homie_property)`

## Key Design Patterns

### Property Synchronization
Properties are synchronized from application to MQTT using callbacks:
```python
homie_property = homie_node.add_property_from_dict(property_dict)
example_device.add_property_on_change_callback(
    group, py_property_id,
    partial(set_homie_property_from_python_property, homie_property))
```

### State Transitions
When modifying device structure (adding/removing nodes or properties):
1. Set device state to `DeviceState.INIT`
2. Make changes and publish new `$description`
3. Set device state to `DeviceState.READY`

Use convenience methods: `begin_state_transition()` and `end_state_transition()`

### MQTT Topic Retention
- Properties track `_ever_published` flag to avoid creating phantom topics
- Use `clear_value()` to properly remove retained topics
- `delete_all_from_mqtt()` cleans up entire device state on shutdown

### Reconnection Handling
- Device publishes Last Will and Testament (LWT) as `DeviceState.LOST`
- `on_connect` callback republishes device state and all property values
- MQTT reconnection delay: 1-30 seconds with exponential backoff

## Configuration

### MQTT Broker Configuration
Broker configuration is loaded from JSON file specified by `EBUS_BROKER_CFG` environment variable or `--config` command line option:

```json
{
  "host": "mqtt.example.com",
  "port": 1883,
  "authentication": {
    "type": "USER_PASS",
    "username": "myuser",
    "password": "secret"
  },
  "use_tls": false
}
```

For MQTTS connections (TLS/SSL) with insecure mode (default):
```json
{
  "host": "span-panel.local",
  "port": 8883,
  "authentication": {
    "type": "USER_PASS",
    "username": "panel-serial-number",
    "password": "mqtt-password"
  },
  "use_tls": true,
  "tls_insecure": true
}
```

For MQTTS with CA certificate verification (secure mode):
```json
{
  "host": "span-panel.local",
  "port": 8883,
  "authentication": {
    "type": "USER_PASS",
    "username": "panel-serial-number",
    "password": "mqtt-password"
  },
  "use_tls": true,
  "tls_ca_cert": "/path/to/ca-cert.crt",
  "tls_insecure": false
}
```

**TLS Options:**
- `use_tls`: Enable TLS/SSL connection
- `tls_ca_cert`: Path to CA certificate file for server verification (optional)
- `tls_insecure`: Skip certificate verification (default: true for backwards compatibility)

When `tls_insecure` is true (or `tls_ca_cert` is not provided), the MqttClient uses TLS 1.2 with certificate verification disabled to support self-signed certificates. When `tls_ca_cert` is provided and `tls_insecure` is false, strict certificate verification is performed.

### Environment Variables
- `EBUS_BROKER_CFG`: Path to broker configuration JSON file
- `EBUS_HOMIE_MQTT_QOS_SITE`: Override default MQTT QoS (default: 2)
- `PUBLIC_MQTT_ENDPOINT`: Default broker endpoint (default: 127.0.0.1)
- `PUBLIC_MQTT_PORT`: Default broker port (default: 1885)

## Running Examples

Examples are in the `examples/` directory. See `examples/README.md` for details.

```bash
# Device example
./examples/simple-device --config /path/to/broker-cfg.json

# Controller example
./examples/simple-controller --config /path/to/broker-cfg.json

# SPAN Panel controller (requires zeroconf)
./examples/simple-span-controller <serial-number> <password>

# With SPAN-API utilities (automatic credentials)
export PYTHONPATH=$PYTHONPATH:~/projects/span.io/span/repo/SPAN-API/lib
./examples/simple-span-controller <serial-number>
```

## Important Implementation Notes

### Thread Safety
All property operations are designed to be thread-safe:
- PythonProperty uses `threading.Lock()` for value mutations
- GroupedPropertyDict locks during group/property access
- MQTT client runs in separate thread via `loop_start()`

### Property Value Publishing
Properties only publish to MQTT when:
1. Value is not None, OR
2. Property has been previously published (`_ever_published == True`)
3. `skip_initial_publish` flag is not set

This prevents creating empty retained topics in the broker.

### Settable Properties
For properties that can be set remotely:
- Set `settable=True` in property definition
- Provide `set_callback` function with signature: `callback(payload) -> None`
- Property automatically subscribes to `{topic}/set`
- For async callbacks, pass `async_loop` parameter

### Child Devices
Child devices must specify both `root_id` and `parent_id`. The implementation notes that child devices likely need to share the MQTT connection with the root device (currently not fully implemented).

## Controller Role

The SDK now includes a **Controller** class that implements the Homie controller role for discovering and interacting with devices.

### Controller Features

- **Auto-discovery**: Subscribe to `+/5/+/$state` to discover all Homie devices
- **Device tracking**: Maintains registry of discovered devices with state, descriptions, and property values
- **Description parsing**: Automatically fetches and parses `$description` JSON for each device
- **Property monitoring**: Subscribes to all property topics and tracks value changes
- **Command sending**: Send commands to settable properties via `/set` topics
- **Broadcasting**: Send broadcast messages to all devices
- **Event callbacks**: Register callbacks for device discovery, state changes, property updates, etc.

### Controller Usage

```python
import json
from ebus_sdk import Controller

# Load broker config
with open('broker-cfg.json', 'r') as f:
    mqtt_cfg = json.load(f)

# Create controller
controller = Controller(mqtt_cfg=mqtt_cfg)

# Register callbacks
controller.set_on_device_discovered_callback(
    lambda dev: print(f"Found device: {dev.device_id}"))
controller.set_on_property_changed_callback(
    lambda dev_id, node, prop, val, old: print(f"{dev_id}/{node}/{prop} = {val}"))

# Start discovery
controller.start_discovery()

# Send command to a device
controller.set_property('device-id', 'node-id', 'property-id', 'value')

# Broadcast message
controller.broadcast('alert', 'System maintenance in 5 minutes')

# Get discovered devices
devices = controller.get_all_devices()
for device_id, device in devices.items():
    print(f"{device_id}: {device.state}")
```

### DiscoveredDevice Class

The `DiscoveredDevice` class represents a device discovered by the controller:

- `device_id`: Device identifier
- `state`: Current device state (init, ready, disconnected, sleeping, lost)
- `description`: Parsed JSON description with nodes and properties
- `properties`: Dictionary of current property values `{node_id: {property_id: value}}`
- `property_targets`: Dictionary of property target values
- `last_seen`: Timestamp of last message received

Helper methods:
- `get_property(node_id, property_id)`: Get current property value
- `get_property_target(node_id, property_id)`: Get property target value
- `get_nodes()`: Get list of node IDs from description
- `get_node_properties(node_id)`: Get properties dict for a node

Note: The `$description` JSON format uses dictionaries (objects) for nodes and properties, not arrays.

## Known Limitations & TODOs

From the codebase comments:
- Thread-safety improvements needed throughout
- Child device support incomplete (SAS-3547)
- `$target` attribute not fully implemented for settable properties
- Empty string value encoding (0x00 byte) not implemented
- Graceful node/property removal needs improvement

## File Organization

```
python-sdk/
├── src/ebus_sdk/           # Package source
│   ├── __init__.py         # Package exports
│   ├── homie.py            # Core Homie convention implementation
│   │                       #   Device, Node, Property (device role)
│   │                       #   Controller, DiscoveredDevice (controller role)
│   │                       #   Enums: DeviceState, PropertyDatatype, Unit
│   ├── mqtt.py             # MQTT client wrapper around paho-mqtt
│   └── property.py         # Application-level property abstractions
├── examples/               # Example scripts
│   ├── simple-device       # Device publishing sensor data
│   ├── simple-controller   # Controller discovering devices
│   └── simple-span-controller  # SPAN Panel controller with mDNS
├── pyproject.toml          # Package configuration (pip install)
├── README.md               # Package documentation
└── CLAUDE.md               # This file
```

## Homie Convention Specifics

- MQTT topic structure: `ebus/5/{device_id}/{node_id}/{property_id}`
- Special topics:
  - `{base}/$state`: Device state (init, ready, disconnected, sleeping, lost)
  - `{base}/$description`: JSON schema of device/nodes/properties
  - `{base}/{node}/{property}/set`: Topic for settable properties
- Retained messages used for device state and property values
- QoS level configurable via environment (default: 2)
