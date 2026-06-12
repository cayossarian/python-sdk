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
from .homie import EBUS_HOMIE_MQTT_QOS, HOMIE_EFFECTIVE_STATE_TABLE

# Utility functions
from .homie import (
    datatype_from_type,
    ebus_cfg_add_auth,
    sanitize_homie_id,
)

# Property abstractions
from .property import (
    Property as ObservableProperty,
    GroupedPropertyDict,
    PropertyDict,
    ChangeEvent,
    BulkUpdateContext,
)

# MQTT client
from ebus_mqtt_client import MqttClient

__version__ = "0.2.2"

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
    # Utilities
    "datatype_from_type",
    "ebus_cfg_add_auth",
    "sanitize_homie_id",
    # Property abstractions
    "ObservableProperty",
    "GroupedPropertyDict",
    "PropertyDict",
    "ChangeEvent",
    "BulkUpdateContext",
    # MQTT
    "MqttClient",
]
