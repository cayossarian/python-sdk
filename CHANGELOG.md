# Changelog

All notable changes to `ebus-sdk` are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Releases earlier than 0.2.0 predate this file — see `git log` and the corresponding `v0.1.x` tags for details.

## [Unreleased]

## [0.4.0] — 2026-06-19

### Added

- Homie 5 empty-string **value** encoding. A property whose value is the empty string `""` is now published as a 1-character payload containing a single null byte (`0x00`), and an inbound `0x00` payload is decoded back to `""`. This is the Homie 5 convention that distinguishes an actual empty-string value from a zero-length payload (which MQTT/Homie treats as "clear the retained topic" — see the retained-clear fix below). Encoding is applied on publish (`Property.publish_value()` for reported values, `Controller.set_property()` for outbound `/set`) and decoding on receive (`Controller._on_property_message()` / `_on_target_message()`, `Property._settable_callback()` for inbound `/set`). New exported helpers `encode_empty_string()` / `decode_empty_string()` and the constant `HOMIE_EMPTY_STRING_PAYLOAD`. The convention provides no way to represent a genuine 1-character `0x00` string value; a device needing that must escape it at the application level. Outbound `$target` encoding (`publish_target_value()`) remains a stub. Closes SDK-kvd.

### Fixed

- Publishers can now clear a **retained** property value. Setting a property to `None` after it has been published emits the empty (zero-length) `retain=True` payload that retracts the value from the broker, instead of the previous silent no-op that left the stale retained value in place (a reconnecting subscriber would read the obsolete value). The never-published-`None` case stays a silent no-op, so no phantom retained-empty topic is created. This fixes the documented adapter/bridge pattern with no consumer change — an application property going `None` now retracts its mirrored Homie topic. The `clear_value()` docstring documents the empty-retained convention. Closes SDK-ef1 / GitHub #2.

### Changed

- `Device.publish_description()` is now a no-op when the description content — ignoring the always-fresh `version` timestamp — is byte-identical to what was last published. This saves the redundant (often multi-KB) `$description` republish and the gratuitous `$state` `init`→`ready` flap it would otherwise drive on subscribers. A forced republish (`republish=True`, used by the reconnect cascade and not-yet-`ready` devices) is exempt and always publishes, so reconnect still restores the retained `$description`. Scope note: this suppresses the redundant `$description` payload, **not** the `$state` `init`→`ready` flap of an otherwise-empty `state_transition()` — the eager-`init` publish is preserved (the documented in-transition state contract), so a no-op transition still emits `init`→`ready` on `$state` while emitting no `$description`. Closes SDK-n83.
- Interim `$description` publishes are deferred while a `state_transition()` is open. Adding N nodes inside one transition now puts exactly one `$description` on the wire — the consolidated publish at transition exit — rather than N+1. The final published description is byte-identical to before; only the redundant interim publishes (immediately superseded by the exit publish) are removed. Closes SDK-9ps.

## [0.3.1] — 2026-06-14

### Fixed

- Tree-rooted Controller: on initial connect to a broker holding retained `$state=ready` and retained `$description`, the state message often arrives before the description (paho delivers retained messages in subscription order and we subscribe to `$state` first). The state-edge reconcile would then see an empty `children` list and subscribe to nothing; the late-arriving description was stashed but never triggered another reconcile. `_on_description_message` now also fires `_reconcile_descendants` when the device is already at `$state=ready` — idempotent, so the design-intended description-then-state ordering still works. Caught during span-hass Phase 4 live verification against a real SPAN G2 panel where the panel root discovered but no descendants did. Closes SDK-gsn.
- Tree-rooted Controller: `_reconcile_descendants` now diffs declared children against an internal `_subscribed_children` registry rather than walking each child's own `parent_id` (which is `None` until that child's `$description` arrives). A pre-created-but-not-yet-described child no longer looks "missing" on a subsequent reconcile, so repeat descriptions in `$state=ready` are now true no-ops.

## [0.3.0] — 2026-06-13

### Added

- `Controller(root_device_id=<id>)` — third discovery mode, complementary to wildcard and single-device. Subscribes to the named root's four topic patterns, then auto-subscribes to each descendant as it's announced via the parent's `$description.children`. Subscription changes are gated on the parent's `$state` init→ready edge: a `$description` arriving while `$state=init` is stashed but not acted on, since per Homie 5 only `ready` confirms the description is current. Reconnect re-walks the tree from the root using paho-mqtt's subscription recovery + a wiped in-memory registry. Solves the SPAN G3P-23496 multi-panel scoping problem where wildcard mode would see every panel on the shared broker and single-device mode would see the panel root but none of its children. Closes SDK-o1h.
- `Controller.is_tree_rooted` — boolean property, True when constructed with `root_device_id`.

### Changed

- Dependency floor: `ebus-mqtt-client>=0.1.6` (was `>=0.1.2`). The tree-rooted mode's recursive descendant teardown needs the `MqttClient.unsubscribe()` method introduced in upstream 0.1.6.

## [0.2.2] — 2026-06-12

### Fixed

- `Device.publish_nodes()` no longer crashes with `RuntimeError: dictionary changed size during iteration` when the main thread adds a node while the MQTT loop thread is publishing on initial broker connect. Iteration now goes through a `list(self._nodes.values())` snapshot — matching the defensive pattern already used in `delete_all_from_mqtt()`. Hit on a SPAN G2 panel immediately after deploy; systemd subsequently SIGKILLed the unresponsive process, restart recovered.
- `Device.refresh_tree()` similarly snapshots `self._children` before recursing. Lists don't raise on mutation-during-iteration, but CPython's list iterator would otherwise pull a half-constructed child added mid-cascade into the current reconnect republish — matching the defensive pattern in `Device.delete()`.

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

[Unreleased]: https://github.com/electrification-bus/python-sdk/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/electrification-bus/python-sdk/releases/tag/v0.4.0
[0.3.1]: https://github.com/electrification-bus/python-sdk/releases/tag/v0.3.1
[0.3.0]: https://github.com/electrification-bus/python-sdk/releases/tag/v0.3.0
[0.2.2]: https://github.com/electrification-bus/python-sdk/releases/tag/v0.2.2
[0.2.1]: https://github.com/electrification-bus/python-sdk/releases/tag/v0.2.1
[0.2.0]: https://github.com/electrification-bus/python-sdk/releases/tag/v0.2.0
[0.1.7]: https://github.com/electrification-bus/python-sdk/releases/tag/v0.1.7
[0.1.2]: https://github.com/electrification-bus/python-sdk/releases/tag/v0.1.2
