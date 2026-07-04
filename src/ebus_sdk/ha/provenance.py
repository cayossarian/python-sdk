"""Loop-avoidance tooling for the HA <-> eBus round trip.

Two independent bridges on one broker can echo each other: a HA->eBus proxy (for
example `ekm-proxy`) mirrors a Home Assistant device onto eBus, and then an
eBus->HA bridge re-emits that mirror back to Home Assistant as a duplicate. This
module provides two layered guards.

Guard A: origin self-echo. Everything the emitter publishes carries a distinctive
HA `origin` (`EBUS_SDK_ORIGIN`). A HA->eBus proxy calls `is_ebus_sdk_origin` on
the parsed discovery and ignores anything the SDK itself emitted, breaking the
HA->eBus->HA cycle at the import boundary with no per-device cooperation.

Guard B: an `imported` Homie extension plus an `imported-from` device attribute
recording the SOURCE ecosystem. A proxy that creates an eBus device FROM an
external discovery mechanism advertises the `energy.ebus.imported` extension in
the device `extensions` list and sets a device-level `imported-from` field in the
`$description` (e.g. `"imported-from": "ha"`). Homie 5 forward-compat requires
controllers to ignore unknown description fields but keep the device (convention
§Forward compatibility), so the attribute is spec-safe. The source lets a
re-exporter be precise rather than blunt: the eBus->HA bridge skips only devices
imported FROM Home Assistant (the actual round trip), so a device imported from,
say, Zigbee is still exportable to HA. Read it with `is_imported` /
`imported_source`.

Homie itself uses "bridge/bridging" for a protocol-gateway device (a parent
fronting child devices) and ties it to loop prevention (see the convention's
`$target` rule). That maps to our proxy/adapter ACTOR; we use "imported" here for
the device's resulting provenance, naming the DIRECTION the marker guards (do not
re-export what was imported). See issue SDK-dn4. The `energy.ebus.imported`
extension awaits a formal Homie extension specification document (see
`../convention/extensions/`).
"""

from __future__ import annotations

from typing import Optional

from .discovery import HADevice, HAOrigin

# Guard A: the origin the emitter stamps on every HA discovery config it
# publishes. A HA->eBus importer ignores configs carrying this origin (its own
# echo). `ebus.energy` is the project home; the name is the stable join key.
EBUS_SDK_ORIGIN_NAME = "ebus-sdk"
EBUS_SDK_ORIGIN = HAOrigin(name=EBUS_SDK_ORIGIN_NAME, support_url="https://ebus.energy")


def is_ebus_sdk_origin(device: Optional[HADevice]) -> bool:
    """True iff a parsed `HADevice`'s origin marks it as SDK-emitted.

    A HA->eBus proxy uses this to skip HA discovery the eBus SDK itself published,
    so it never re-imports its own output (Guard A).
    """
    return bool(device and device.origin and device.origin.name == EBUS_SDK_ORIGIN_NAME)


# Guard B: the Homie extension a reverse proxy advertises on an eBus device it
# imported from an external ecosystem, plus the description attribute carrying the
# source. The extension id is fixed (source lives in the attribute, not the id);
# the `$extensions` entry follows the Homie convention `<id>:<version>:[<homie>]`.
EBUS_IMPORTED_EXTENSION = "energy.ebus.imported"
_EBUS_IMPORTED_EXTENSION_VERSION = "1.0.0"

# The device-level `$description` field the extension defines: the source
# ecosystem a device was imported from.
IMPORTED_FROM_ATTRIBUTE = "imported-from"

# Stable source-ecosystem tokens (the `imported-from` value). Home Assistant is
# the one the HA bridge guards against; others are conventional labels a proxy
# picks.
HA_ECOSYSTEM = "ha"


def imported_extension(*, version: str = _EBUS_IMPORTED_EXTENSION_VERSION) -> str:
    """The `energy.ebus.imported` `$extensions` entry a reverse proxy advertises.

    Add the return value to the eBus device's `extensions` list (e.g.
    `Device(..., extensions=[imported_extension()])`). Pair it with
    `imported_from_attribute(source)` in `description_extras` to record which
    ecosystem the device was imported from.
    """
    return f"{EBUS_IMPORTED_EXTENSION}:{version}:[5.x]"


def imported_from_attribute(source: str) -> dict:
    """The `imported-from` description-attribute fragment for `description_extras`.

    e.g. `Device(..., extensions=[imported_extension()],
    description_extras=imported_from_attribute(HA_ECOSYSTEM))` marks the device as
    imported from Home Assistant. `source` is a stable ecosystem token
    (`"ha"`, `"zigbee"`, ...).
    """
    return {IMPORTED_FROM_ATTRIBUTE: source}


def _description_of(device_or_description) -> dict:
    """The `$description` dict from a DiscoveredDevice or a raw description dict."""
    description = getattr(device_or_description, "description", device_or_description)
    return description if isinstance(description, dict) else {}


def _advertises_imported_extension(description: dict) -> bool:
    exts = description.get("extensions")
    if not isinstance(exts, list):
        return False
    return any(isinstance(e, str) and e.split(":", 1)[0] == EBUS_IMPORTED_EXTENSION for e in exts)


def is_imported(device_or_description) -> bool:
    """True iff a device is marked as imported from another ecosystem (Guard B).

    Accepts a `DiscoveredDevice` or a raw `$description` dict. A device is
    considered imported if it advertises the `energy.ebus.imported` extension OR
    carries a non-empty `imported-from` attribute (the compliant form has both).
    """
    description = _description_of(device_or_description)
    if _advertises_imported_extension(description):
        return True
    return bool(description.get(IMPORTED_FROM_ATTRIBUTE))


def imported_source(device_or_description) -> Optional[str]:
    """The source ecosystem a device was imported from, or None.

    Returns the `imported-from` attribute value (e.g. `"ha"`, `"zigbee"`), or None
    when the device is not imported OR advertises the extension without naming a
    source. Use `is_imported` to distinguish those cases.
    """
    value = _description_of(device_or_description).get(IMPORTED_FROM_ATTRIBUTE)
    return value if value else None
