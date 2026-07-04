"""
ebus-sdk: Python SDK for Homie MQTT Convention (eBus)

This SDK provides Device and Controller roles for the Homie MQTT convention.
"""

# Core Homie classes
from .homie import (
    Device,
    Node,
    Property,
    Controller,
    DiscoveredDevice,
    StateTransitionContext,
)

# Enums
from .homie import (
    DeviceState,
    PropertyDatatype,
    Unit,
)

# Constants
from .homie import (
    EBUS_HOMIE_MQTT_QOS,
    HOMIE_EFFECTIVE_STATE_TABLE,
    HOMIE_EMPTY_STRING_PAYLOAD,
)

# Utility functions
from .homie import (
    datatype_from_type,
    ebus_cfg_add_auth,
    sanitize_homie_id,
    encode_empty_string,
    decode_empty_string,
)

# Property abstractions
from .property import (
    Property as ObservableProperty,
    GroupedPropertyDict,
    PropertyDict,
    ChangeEvent,
    BulkUpdateContext,
)

# Proxy / adapter helpers (see doc/building-a-proxy.md)
from .adapter import (
    bind_property_to_homie,
    set_homie_property_from_python_property,
)

# Declarative property specs + builder (see doc/building-a-proxy.md)
from .declaration import (
    PropertySpec,
    build_from_declarations,
    python_type_for,
)

# MQTT client
from ebus_mqtt_client import MqttClient

__version__ = "0.6.0"

__all__ = [
    # Homie classes
    "Device",
    "Node",
    "Property",
    "Controller",
    "DiscoveredDevice",
    "StateTransitionContext",
    # Enums
    "DeviceState",
    "PropertyDatatype",
    "Unit",
    # Constants
    "EBUS_HOMIE_MQTT_QOS",
    "HOMIE_EFFECTIVE_STATE_TABLE",
    "HOMIE_EMPTY_STRING_PAYLOAD",
    # Utilities
    "datatype_from_type",
    "ebus_cfg_add_auth",
    "sanitize_homie_id",
    "encode_empty_string",
    "decode_empty_string",
    # Property abstractions
    "ObservableProperty",
    "GroupedPropertyDict",
    "PropertyDict",
    "ChangeEvent",
    "BulkUpdateContext",
    # Proxy / adapter helpers
    "set_homie_property_from_python_property",
    "bind_property_to_homie",
    # Declarative specs + builder
    "PropertySpec",
    "build_from_declarations",
    "python_type_for",
    # MQTT
    "MqttClient",
]
