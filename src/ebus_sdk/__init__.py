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

# Utility functions
from .homie import (
    datatype_from_type,
    ebus_cfg_add_auth,
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
from .mqtt import MqttClient

__version__ = "0.1.0"

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
    # Utilities
    "datatype_from_type",
    "ebus_cfg_add_auth",
    # Property abstractions
    "ObservableProperty",
    "GroupedPropertyDict",
    "PropertyDict",
    "ChangeEvent",
    "BulkUpdateContext",
    # MQTT
    "MqttClient",
]
