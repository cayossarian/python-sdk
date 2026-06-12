# Changelog

All notable changes to `ebus-sdk` are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Releases earlier than 0.2.0 predate this file — see `git log` and the corresponding `v0.1.x` tags for details.

## [Unreleased]

## [0.2.1] — 2026-06-12

### Added

- `ebus_sdk.sanitize_homie_id(value)` — coerce an arbitrary vendor-supplied string (serial number, model name, etc.) to a Homie-legal id segment matching `[a-z0-9-]+`. Lowercases, maps underscores / whitespace / dots to hyphens, drops other illegal characters, collapses hyphen runs, strips leading/trailing hyphens. Single source of truth so a publisher composing a child device-id and a consumer composing the same id-as-a-pointer can never disagree. Replaces (and obsoletes) the ad-hoc `_sanitize_homie_id` copies that had begun to accumulate in downstream consumers.

## [0.2.0] — 2026-06-11

The 0.2.0 release introduces first-class parent/child device trees on both the device (publisher) and controller (consumer) sides of the SDK. A panel-style root device can now own dozens of child devices that share a single MQTT connection and a single Last Will, and controllers can navigate the resulting tree and compute effective state per the Homie 5 spec without re-implementing the rules per consumer.

### Added

- `Device(parent=<Device>)` constructor argument for building child devices. The child borrows the root's MQTT connection — there is exactly one `MqttClient` per tree and exactly one Last Will registered, on the root's `$state` topic.
- `Device.root()`, `Device.parent()`, `Device.children()` — live references that walk the tree. `root_id()` / `parent_id()` accessors are now derived from these. Trees may be arbitrary depth (e.g. panel → BESS child → MID grandchild).
- `Device.delete()` — runs the Homie remove-child protocol (clear retained `$state` / `$description` / property values, detach from parent, re-publish parent's description). Recursive on roots: leaves-first teardown of the whole subtree.
- `Device.refresh_tree()` — recursive republish of description + nodes + state for every device under this one. Called automatically by `on_connect()` on broker reconnect so the entire tree's retained-state is re-established.
- `DiscoveredDevice.root_id`, `parent_id`, `children_ids`, `is_root` — hierarchy fields derived from `$description`.
- `Controller.get_root_devices()`, `get_root(device_id)`, `get_children(device_id)`, `get_descendants(device_id)` — tree-navigation API.
- `Controller.get_effective_state(device_id)` and the public `HOMIE_EFFECTIVE_STATE_TABLE` module constant — implement the Homie 5 state-precedence rule. When a root is `init` / `disconnected` / `sleeping` / `lost`, every descendant is effectively the same state without each descendant needing to republish.

### Changed

- **BREAKING** — `Device` constructor signature. The previous string-ID args `root_id=`, `parent_id=`, and `children_ids=` are removed; use `parent=<Device>` instead. Pure root-device usage (`Device(id, name, type, mqtt_cfg, ...)`) is unchanged and source-compatible.
- The `Device._end_state_transition()` → broker sequence now emits exactly one `$state=init` and one `$state=ready` regardless of nesting depth (state transitions are reentrant via an internal depth counter). Previously a nested `with device.state_transition(): with device.state_transition(): ...` emitted a spurious extra INIT/READY cycle from the outer `__exit__`, forcing every controller in the wild to resync unnecessarily.
- `add_node()` and other structural mutations inside `with parent.state_transition(): ...` now collapse into a single parent `$state=init` → `$description` → `$state=ready` cycle for the whole batch. Building a panel with 32 circuit children produces one observable parent transition, not 32.
- `Device.publish()`, `delete_all_from_mqtt()`, `clear_retained_topic()`, and the `Property` → `Node` → `Device` publish chain all route through `Device.get_mqtt_client()`, which ascends to `root().mqttc`. There is no behavioral change for root devices; child devices now publish their topics through the root's connection automatically.

### Removed

- **BREAKING** — `Device.add_child(child_id)`. Children are added by constructing them with `parent=<Device>`; the `_children` list is maintained automatically.
- **BREAKING** — `Device.remove_child(child_id)`. Use `child_device.delete()` instead — it runs the full Homie remove-child protocol rather than just popping an ID from a list.
- **BREAKING** — `Device.set_parent(parent_id)` and `Device.unset_parent()`. Homie does not support reparenting at the wire level; destroy and reconstruct instead.
- **BREAKING** — `Device.refresh_all_nodes()`. Renamed to `refresh_tree()` to reflect that it now walks descendants. The old name was misleading: it always published `$description` and `$state` in addition to nodes.

### Fixed

- Recursive `Device.delete()` no longer emits gratuitous `$state=init` / `$state=ready` publishes on dying intermediate devices while the cascade is in progress.
- Broker reconnect now republishes every device in the tree (description, nodes, property values, state), not just the root.

## [0.1.7] — 2025

### Fixed

- Added `setup.py` shim for legacy setuptools toolchains that can't read `pyproject.toml`-only packages.

## [0.1.2] — 2025

Initial public release on PyPI. See `git log v0.1.2` for the surface that shipped.

[Unreleased]: https://github.com/electrification-bus/python-sdk/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/electrification-bus/python-sdk/releases/tag/v0.2.1
[0.2.0]: https://github.com/electrification-bus/python-sdk/releases/tag/v0.2.0
[0.1.7]: https://github.com/electrification-bus/python-sdk/releases/tag/v0.1.7
[0.1.2]: https://github.com/electrification-bus/python-sdk/releases/tag/v0.1.2
