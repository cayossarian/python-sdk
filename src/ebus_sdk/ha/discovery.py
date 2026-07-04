"""Home Assistant MQTT discovery parser (vendor-neutral, stdlib-only).

Parses Home Assistant MQTT *device-based* discovery messages
(`<prefix>/device/<object_id>/config`) into a normalized, self-describing
model: an `HADevice` carrying device/origin metadata plus a map of
`HAComponent` entities. Also handles the abbreviated key form, the `~`
base-topic macro, `value_template` field recovery, availability (single and
list forms), and removal / migration control messages.

Deliberately free of any vendor coupling and free of any MQTT, eBus, or Homie
imports: it turns raw discovery structure into a plain, neutral data model and
nothing more. That neutrality is what lets any eBus proxy share this HA -> eBus
front door (map `HAComponent` semantics onto eBus properties via
`ebus_sdk.ha.semantics.derive_spec`). Spec reference: `doc/ha-mqtt-discovery.md`.

The parse-only direction lives here; the reverse (serialize a Homie/eBus device
back into HA discovery config) is planned as a sibling `emit` module reusing
this same neutral model (see issue SDK-dn4). Keep parsing and serializing
separate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Union

# Short -> long key expansions, resolved WITHIN a scope (the same short key can
# mean different things in different blocks; `sw` is `sw_version` in both device
# and origin, `name` recurs everywhere, so never expand globally).

_DEVICE_ABBR = {
    "ids": "identifiers",
    "cns": "connections",
    "mf": "manufacturer",
    "mdl": "model",
    "mdl_id": "model_id",
    "sw": "sw_version",
    "hw": "hw_version",
    "sn": "serial_number",
    "cu": "configuration_url",
    "sa": "suggested_area",
}

_ORIGIN_ABBR = {
    "sw": "sw_version",
    "url": "support_url",
}

# Component / root scope. Includes the keys a robust parser must recognize even
# if it does not act on all of them; unknown keys are tolerated and preserved.
_COMPONENT_ABBR = {
    "p": "platform",
    "dev": "device",
    "o": "origin",
    "cmps": "components",
    "uniq_id": "unique_id",
    "dev_cla": "device_class",
    "unit_of_meas": "unit_of_measurement",
    "stat_cla": "state_class",
    "val_tpl": "value_template",
    "stat_t": "state_topic",
    "stat_tpl": "state_template",
    "stat_val_tpl": "state_value_template",
    "avty": "availability",
    "avty_t": "availability_topic",
    "avty_tpl": "availability_template",
    "avty_mode": "availability_mode",
    "pl_avail": "payload_available",
    "pl_not_avail": "payload_not_available",
    "json_attr_t": "json_attributes_topic",
    "json_attr_tpl": "json_attributes_template",
    "json_attr": "json_attributes",
    "ic": "icon",
    "en": "enabled_by_default",
    "ent_cat": "entity_category",
    "exp_aft": "expire_after",
    "frc_upd": "force_update",
    "sug_dsp_prc": "suggested_display_precision",
    "dsp_prc": "display_precision",
    "e": "encoding",
    "cmd_t": "command_topic",
    "lrst_t": "last_reset_topic",
    "lrst_val_tpl": "last_reset_value_template",
    "ops": "options",
    "migr_discvry": "migrate_discovery",
}

_AVAILABILITY_ITEM_ABBR = {
    "t": "topic",
    "pl_avail": "payload_available",
    "pl_not_avail": "payload_not_available",
    "val_tpl": "value_template",
}

_DEFAULT_PAYLOAD_AVAILABLE = "online"
_DEFAULT_PAYLOAD_NOT_AVAILABLE = "offline"

# Recover the field selected by a `value_template`. We only need the JSON path
# referenced as `value_json.<path>` (dotted or bracketed); surrounding Jinja
# filters / functions are irrelevant to the field name and are ignored.
_VALUE_JSON_ANCHOR = re.compile(r"value_json\b")
_ACCESSOR = re.compile(
    r"""\s*(?:\.\s*([A-Za-z_][A-Za-z0-9_]*)      # .field
        |\[\s*(['"])([^'"]+)\2\s*\])             # ['field'] or ["field"]
    """,
    re.VERBOSE,
)


@dataclass
class HAOrigin:
    name: Optional[str] = None
    sw_version: Optional[str] = None
    support_url: Optional[str] = None


@dataclass
class HAAvailabilitySource:
    topic: str
    payload_available: str = _DEFAULT_PAYLOAD_AVAILABLE
    payload_not_available: str = _DEFAULT_PAYLOAD_NOT_AVAILABLE
    value_template: Optional[str] = None


@dataclass
class HAAvailability:
    sources: list[HAAvailabilitySource] = field(default_factory=list)
    mode: str = "latest"  # latest | all | any


@dataclass
class HAComponent:
    key: str  # the `cmps` map key (component object id)
    platform: Optional[str] = None  # "sensor", "binary_sensor", ...
    unique_id: Optional[str] = None
    name: Optional[str] = None
    device_class: Optional[str] = None
    unit_of_measurement: Optional[str] = None
    state_class: Optional[str] = None
    state_topic: Optional[str] = None  # effective (component or inherited), `~` expanded
    value_template: Optional[str] = None  # raw template, retained for debugging
    value_field: Optional[str] = None  # recovered value_json.<path>, e.g. "kWh_Tot"
    scalar_value: bool = False  # template reads bare `value` (non-JSON payload)
    availability: Optional[HAAvailability] = None
    removed: bool = False  # platform-only entry == explicit removal
    config: dict = field(default_factory=dict)  # full expanded config (forward-compat)


@dataclass
class HADevice:
    identifiers: list[str] = field(default_factory=list)
    name: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    model_id: Optional[str] = None
    sw_version: Optional[str] = None
    hw_version: Optional[str] = None
    serial_number: Optional[str] = None
    via_device: Optional[str] = None
    origin: Optional[HAOrigin] = None
    state_topic: Optional[str] = None  # device-root shared state_topic, `~` expanded
    availability: Optional[HAAvailability] = None  # device-level, shared by components
    components: dict[str, HAComponent] = field(default_factory=dict)
    config: dict = field(default_factory=dict)  # full expanded root payload

    @property
    def primary_id(self) -> Optional[str]:
        """Best stable device identifier: serial number, else first identifier."""
        return self.serial_number or (self.identifiers[0] if self.identifiers else None)


@dataclass
class HARemoval:
    """An empty retained payload: the device discovery entry was deleted."""

    object_id: Optional[str] = None


ParseResult = Union[HADevice, HARemoval, None]


def _expand(d: dict, table: dict) -> dict:
    """Expand short keys to long within one scope; long keys pass through."""
    return {table.get(k, k): v for k, v in d.items()}


def _apply_base_topic(config: dict, base: Optional[str] = None) -> dict:
    """Substitute the `~` base-topic macro into any `*_topic` value.

    Per the HA convention, a leading or trailing `~` in a topic value is
    replaced with the base topic. A config's own `~` wins; otherwise `base`
    (the device-root `~`, shared with components) is used. Operates on
    already-expanded (long) keys.
    """
    base = config.get("~") or base
    if not base:
        return config
    out = {}
    for k, v in config.items():
        if k.endswith("_topic") and isinstance(v, str):
            if v.startswith("~"):
                v = base + v[1:]
            elif v.endswith("~"):
                v = v[:-1] + base
        out[k] = v
    return out


def extract_value_field(template: Optional[str]) -> tuple[Optional[str], bool]:
    """Recover the source field a `value_template` selects.

    Returns `(field_path, scalar)`:
      - `field_path` is the dotted `value_json.<path>` reference (e.g. "kWh_Tot"
        or "Timer1.Arm"), or None if none is present.
      - `scalar` is True when the template references the bare `value` variable
        (a non-JSON, scalar state payload) rather than `value_json`.
    """
    if not template:
        return None, False
    anchor = _VALUE_JSON_ANCHOR.search(template)
    if not anchor:
        # No JSON access; a bare `value` reference means a scalar payload.
        scalar = re.search(r"\bvalue\b", template) is not None
        return None, scalar
    parts: list[str] = []
    pos = anchor.end()
    while True:
        m = _ACCESSOR.match(template, pos)
        if not m:
            break
        parts.append(m.group(1) or m.group(3))
        pos = m.end()
    if not parts:
        return None, False
    return ".".join(parts), False


def _parse_availability(config: dict) -> Optional[HAAvailability]:
    """Build a normalized availability model from single-topic or list form."""
    if "availability_topic" in config:
        src = HAAvailabilitySource(
            topic=config["availability_topic"],
            payload_available=config.get("payload_available", _DEFAULT_PAYLOAD_AVAILABLE),
            payload_not_available=config.get("payload_not_available", _DEFAULT_PAYLOAD_NOT_AVAILABLE),
            value_template=config.get("availability_template"),
        )
        return HAAvailability(sources=[src], mode=config.get("availability_mode", "latest"))
    avty = config.get("availability")
    if isinstance(avty, list):
        sources = []
        for item in avty:
            if not isinstance(item, dict):
                continue
            item = _expand(item, _AVAILABILITY_ITEM_ABBR)
            topic = item.get("topic")
            if not topic:
                continue
            sources.append(
                HAAvailabilitySource(
                    topic=topic,
                    payload_available=item.get("payload_available", _DEFAULT_PAYLOAD_AVAILABLE),
                    payload_not_available=item.get("payload_not_available", _DEFAULT_PAYLOAD_NOT_AVAILABLE),
                    value_template=item.get("value_template"),
                )
            )
        if sources:
            return HAAvailability(sources=sources, mode=config.get("availability_mode", "latest"))
    return None


def _parse_device_block(dev: dict) -> dict:
    """Expand the device block and normalize identifiers to a list."""
    dev = _expand(dev, _DEVICE_ABBR)
    ids = dev.get("identifiers")
    if isinstance(ids, str):
        dev["identifiers"] = [ids]
    elif isinstance(ids, list):
        dev["identifiers"] = [str(i) for i in ids]
    return dev


def _parse_origin_block(o: dict) -> HAOrigin:
    o = _expand(o, _ORIGIN_ABBR)
    return HAOrigin(name=o.get("name"), sw_version=o.get("sw_version"), support_url=o.get("support_url"))


def _parse_component(
    key: str,
    raw: dict,
    root_state_topic: Optional[str],
    root_availability: Optional[HAAvailability],
    root_base_topic: Optional[str] = None,
) -> HAComponent:
    cfg = _apply_base_topic(_expand(raw, _COMPONENT_ABBR), base=root_base_topic)
    keys_beyond_platform = [k for k in cfg if k != "platform"]
    removed = not keys_beyond_platform  # {"p": "sensor"} only == explicit removal
    value_field, scalar = extract_value_field(cfg.get("value_template"))
    availability = _parse_availability(cfg) or root_availability
    return HAComponent(
        key=key,
        platform=cfg.get("platform"),
        unique_id=cfg.get("unique_id"),
        name=cfg.get("name"),
        device_class=cfg.get("device_class"),
        unit_of_measurement=cfg.get("unit_of_measurement"),
        state_class=cfg.get("state_class"),
        state_topic=cfg.get("state_topic", root_state_topic),
        value_template=cfg.get("value_template"),
        value_field=value_field,
        scalar_value=scalar,
        availability=availability,
        removed=removed,
        config=cfg,
    )


def parse_device_config(payload: Union[str, bytes, dict, None], object_id: Optional[str] = None) -> ParseResult:
    """Parse one `<prefix>/device/<object_id>/config` discovery message.

    Accepts the raw MQTT payload (JSON str/bytes) or an already-parsed dict.
    Returns:
      - `HARemoval` for an empty retained payload (the entry was deleted),
      - `None` for a migration control message (`migrate_discovery: true`) or an
        unparseable / non-device payload,
      - `HADevice` otherwise.
    """
    if payload is None:
        return HARemoval(object_id=object_id)
    if isinstance(payload, (str, bytes)):
        text = payload.decode() if isinstance(payload, bytes) else payload
        if text.strip() == "":
            return HARemoval(object_id=object_id)
        try:
            payload = json.loads(text)
        except (ValueError, UnicodeDecodeError):
            return None
    if not isinstance(payload, dict):
        return None
    if not payload:
        return HARemoval(object_id=object_id)

    root = _apply_base_topic(_expand(payload, _COMPONENT_ABBR))
    if root.get("migrate_discovery") is True:
        return None

    dev_raw = root.get("device")
    if not isinstance(dev_raw, dict):
        return None  # device-based discovery requires a device block
    dev = _parse_device_block(dev_raw)

    origin = None
    if isinstance(root.get("origin"), dict):
        origin = _parse_origin_block(root["origin"])

    root_state_topic = root.get("state_topic")
    root_availability = _parse_availability(root)

    device = HADevice(
        identifiers=dev.get("identifiers", []),
        name=dev.get("name"),
        manufacturer=dev.get("manufacturer"),
        model=dev.get("model"),
        model_id=dev.get("model_id"),
        sw_version=dev.get("sw_version"),
        hw_version=dev.get("hw_version"),
        serial_number=dev.get("serial_number"),
        via_device=dev.get("via_device"),
        origin=origin,
        state_topic=root_state_topic,
        availability=root_availability,
        config=root,
    )

    components = root.get("components")
    if isinstance(components, dict):
        for key, raw in components.items():
            if not isinstance(raw, dict):
                continue
            device.components[key] = _parse_component(
                key, raw, root_state_topic, root_availability, root_base_topic=root.get("~")
            )
    return device


def read_field(payload: dict, path: Optional[str]) -> Any:
    """Read a (possibly dotted) field from a decoded state payload.

    `path` is a value recovered by `extract_value_field`, e.g. "kWh_Tot" or
    "Timer1.Arm". Returns None if any segment is missing.
    """
    if not path:
        return None
    cur: Any = payload
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur
