# This may be removed in Python 3.10+.
from __future__ import annotations

"""
Classes and Enums to support Homie (version 5)

   https://github.com/spanio/eBus-MQTT-Convention
   https://github.com/homieiot/convention
   https://homieiot.github.io
   https://homieiot.github.io/specification/

This initial version is focused on providing a Homie representation for some entity(s)
Support for Homie "clients" is TBD/future-work, e.g. discovery, etc.

This is the initial version, there are things to add in the future (as needed):
* Make getting and setting a property's value thread-safe, and add thread-safety throughout
* Support for child devices [https://spanio.atlassian.net/browse/SAS-3547]
    Likely there will be a need to share the MQTT connection between parent and child devices, TBD how
* Support for the target attribute for Properties
* Graceful removal of a Device, including its Nodes and their Properties
    Devices can remove old properties and nodes by deleting the respective MQTT topics
    by publishing an empty message to those topics
    (an actual empty string on MQTT level, so NOT the escaped 0x00 byte, see also empty string values)
    https://github.com/eclipse-paho/paho.mqtt.python/blob/master/examples/client_mqtt_clear_retain.py#L43
* Support/handle empty string values:
    MQTT will treat an empty string payload as a “delete” instruction for the topic,
    therefore an empty string value is represented by a 1-character string containing a single byte value 0 (Hex: 0x00, Dec: 0).
    The empty string (passed as an MQTT payload) can only occur in 3 places;
        homie / 5 / [device ID] / [node ID] / [property ID]; reported property values (for string types)
        homie / 5 / [device ID] / [node ID] / [property ID] / set; the topic to set properties (of string types)
        homie / 5 / [device ID] / [node ID] / [property ID] / $target; the target property value (for string types)
    This convention specifies no way to represent an actual value of a 1-character string with a single byte 0.
    If a device needs this, then it should provide an escape mechanism on the application level.
* Given that Nodes and Properties belong to, and contain pointers to, the owning Device (and Node, for Properties),
    seems likely that we can leverage that to obtain the MQTT client (mqttc) of the owning Device, instead of
    having all downstream entities maintain a local pointer to that
"""

import asyncio
import json
import logging
import os
import time
from enum import Enum
# Workaround due to non-support of StrEnum in current Gen2 FW Python, StrEnum available in enum, remove
from spancommon.strenum import StrEnum
from functools import partial
from deprecated import deprecated
from typing import Any, Callable, List, Optional, Union
from spancommon.mqtt import MqttClient


# eBus MQTT topic constants
EBUS_HOMIE_DOMAIN = 'ebus'
EBUS_HOMIE_VERSION_MAJOR = 5
EBUS_HOMIE_VERSION_MINOR = 0
EBUS_HOMIE_VERSION_PATCH = 0
EBUS_HOMIE_MQTT_QOS_DEFAULT = "2"

EBUS_HOMIE_MQTT_QOS = int(os.environ.get('EBUS_HOMIE_MQTT_QOS_SITE', EBUS_HOMIE_MQTT_QOS_DEFAULT))

if EBUS_HOMIE_MQTT_QOS < 1:
    logging.warning(f'reason=homieQosLessThanOne,specifiedQos={EBUS_HOMIE_MQTT_QOS},defaultQos={EBUS_HOMIE_MQTT_QOS_DEFAULT}')

#eBus MQTT broker constants
EBUS_BROKER_DEFAULT_ENDPOINT = os.environ.get('PUBLIC_MQTT_ENDPOINT', '127.0.0.1')
EBUS_BROKER_DEFAULT_PORT     = int(os.environ.get('PUBLIC_MQTT_PORT', '1885'))

USER_PASS_TYPE = 'USER_PASS'

# Helper character constants for units
UNICODE_DEGREE         = '\u00b0'
UNICODE_EXPONENT_3     = '\u00b3'
UNICODE_EXPONENT_MINUS = '\u207b'
UNICODE_EXPONENT_1     = '\u00b9'

class Unit(StrEnum):
    DEGREE_CELSIUS = UNICODE_DEGREE + 'C'
    DEGREE_FAHRENHEIT = UNICODE_DEGREE + 'F'
    DEGREE = UNICODE_DEGREE
    LITER = 'L'
    GALLON = 'gal'
    VOLTS = 'V'
    WATT = 'W'
    KILOWATT = 'kW'
    KILOWATT_HOUR = 'kWh'
    AMPERE = 'A'
    HERTZ = 'Hz'
    REVOLUTIONS_PER_MINUTE = 'rpm'
    PERCENT = '%'
    METER = 'm'
    CUBIC_METER = 'm' + UNICODE_EXPONENT_3
    FEET = 'ft'
    METERS_PER_SECOND = 'm/s'
    KNOTS = 'kn'
    PASCAL = 'Pa'
    POUNDS_PER_SQUARE_INCH = 'psi'
    PARTS_PER_MILLION = 'ppm'
    SECONDS = 's'
    MINUTES = 'min'
    HOURS = 'h'
    LUX = 'lx'
    KELVIN = 'K'
    MIRED = 'MK' + UNICODE_EXPONENT_MINUS + UNICODE_EXPONENT_1
    COUNT_OR_AMOUNT = '#'
    # VOLT_AMPERE_REACTIVE not in Homie specification, but we need it
    # https://github.com/homieiot/convention/issues/318
    VOLT_AMPERE_REACTIVE = 'var'
    WATT_HOUR = 'Wh'


class PropertyDatatype(StrEnum):
    """
    https://homieiot.github.io/specification/
    PropertyDatatype.STRING.value -> 'string'
    PropertyDatatype[foo].value -> 'string' for foo == 'STRING'
    PropertyDatatype('string').name -> 'STRING'
    """
    INTEGER = 'integer'
    FLOAT = 'float'
    BOOLEAN = 'boolean'
    STRING = 'string'
    ENUM = 'enum'
    COLOR = 'color'
    DATETIME = 'datetime'
    DURATION = 'duration'
    JSON = 'json'


def datatype_from_type(type: Type) -> Optional[PropertyDatatype]:
    """
    Returns Homie PropertyDatatype from Python type
    PropertyDatatypes with no native Python type are specified as strings
    """
    if type == int:
        return PropertyDatatype.INTEGER
    elif type == float:
        return PropertyDatatype.FLOAT
    elif type == bool:
        return PropertyDatatype.BOOLEAN
    elif type == str:
        return PropertyDatatype.STRING
    elif type == StrEnum:
        return PropertyDatatype.ENUM
    elif type == 'color':
        return PropertyDatatype.COLOR
    elif type == 'datetime':
        return PropertyDatatype.DATETIME
    elif type == 'duration':
        return PropertyDatatype.DURATION
    elif type == 'json':
        return PropertyDatatype.JSON
    else:
        logging.warning(f'reason=datatypeFromTypeUnknownType,type={type}')
        return None


class DeviceState(StrEnum):
    """
    https://homieiot.github.io/specification/
    DeviceState.READY.value -> 'ready'
    DeviceState[foo].value -> 'ready' for foo == 'READY'
    DeviceState('ready').name -> 'READY'
    """
    INIT = 'init'
    READY = 'ready'
    DISCONNECTED = 'disconnected'
    SLEEPING = 'sleeping'
    LOST = 'lost'


class Property:
    """
    Object representing a Homie MQTT Property
    https://homieiot.github.io/specification/
    https://github.com/spanio/ebus-mqtt-convention/blob/main/CONVENTION.md
    Note that device and node are NOT overwritten if they exist
    Question: Should we subclass per datatype???
    TODO: Should device_id come from Node -> Device -> id?
    TODO: Fail loudly if "id" not provided
    """
    def __init__(self,
                 id: Optional[str] = None,
                 value: Optional[Any] = None,
                 name: Optional[str] = None,
                 datatype: PropertyDatatype = None,
                 format: Optional[str] = None,
                 settable: Optional[bool] = False,
                 set_callback: Optional[Callable] = None,
                 retained: Optional[bool] = True,
                 unit: Optional[str] = None,
                 round_to: Optional[int] = None,
                 supports_target: Optional[bool] = False,
                 node: Optional[Node] = None,
                 device: Optional[Device] = None,
                 async_loop: Optional[asyncio.SelectorEventLoop] = False,
                 from_dict: Optional[dict] = None
                 ):
        if from_dict:
            # from_dict not tiven
            id = from_dict.get('id', None)
            value = from_dict.get('value', None)
            name = from_dict.get('name', None)
            datatype = from_dict.get('datatype', None)
            format = from_dict.get('format', None)
            settable = from_dict.get('settable', False)
            retained = from_dict.get('retained', True)
            unit = from_dict.get('unit', None)
            round_to = from_dict.get('round_to', None)
            supports_target = from_dict.get('supports_target', False)
            node = from_dict.get('node', None)
            device = from_dict.get('device', None)
            set_callback = from_dict.get('set_callback', None)
            async_loop = from_dict.get('async_loop', None)
        # Regardless of how we got this info, construct it
        # AKA, the "business logic" of the constructor
        self._id = id
        self._round = round_to
        self._value = value
        if name:
            self._name = name
        else:
            self._name = id
        self._datatype = datatype
        self._format = format
        self._settable = settable
        # Don't assign set_callback unless this property is settable
        if settable:
            self._set_callback = set_callback
        else:
            self._set_callback = None
        self._retained = retained
        self._unit = unit
        self._supports_target = supports_target
        self._node = node
        self._device = device
        self.async_loop = async_loop

    @staticmethod
    @deprecated
    def from_dict(property_dict: dict) -> Property:
        """
        Deprecated, use Property(from_dict = some_dict)
        """
        return Property(from_dict=property_dict)

    def set_node(self, node: Node) -> None:
        self._node = node

    def node(self) -> Node:
        """
        Returns Node containing Property
        """
        return self._node

    @deprecated
    def get_node(self) -> Node:
        """
        Deprecated, use node()
        """
        return self.node()

    def get_node_id(self) -> str:
        """
        Why is this needed?
        do my_property.node().id()
        TODO: Find callers and change them!
        """
        node = self.node()
        if not node:
            logging.warning(f'reason=propertyGetNodeNoNode,propertyID={self._id}')
            return None
        return self.node().id()

    def get_device_id(self) -> str:
        """
        Why is this needed?
        do my_property.device().id()
        TODO: Find callers and change them!
        """
        node = self.node()
        if not node:
            logging.warning(f'reason=propertyGetDeviceIdNoNode,propertyID={self._id}')
            return None
        # return node.get_device_id() # TODO how about node.device().id()
        return node.device().id()

    def set_device(self, device: Device) -> None:
        self._device = device
        return None

    def set_value(self, value: Any) -> bool:
        """
        Set the property's value to value, and publishes the new value to MQTT
        Returns False on failure, else True
        """
        self._value = value
        return self.publish_value()

    @deprecated
    def set(self, value: Any) -> bool:
        """
        Deprecated, call set_value() instead!
        """
        return self.set_value(value)

    def round(self) -> Optional[int]:
        """
        Returns the property's round attribute
        """
        return self._round

    def value(self) -> Any:
        """
        Returns the property's value, potentially rounded
        """
        # TODO: Decide if we really want this to round()
        round_to = self.round()
        if round_to:
            rounded_value = round(self._value, round_to)
            logging.debug(f'reason=propertyGetRounding,id={self._id},rounded={rounded_value},value={self._value}')
            return rounded_value
        else:
            return self._value

    def format(self) -> str:
        """
        Returns format of Property
        """
        return self._format

    @deprecated
    def get(self) -> Any:
        """
        Deprecated, user value()
        Returns the property's value, potentially rounded
        """
        return self.value()

    def coerced_value(self) -> str:
        """
        Returns the property's value (potentially rounded), as a string
        """
        property_value = self.value()
        property_type = self.datatype()
        if property_type == PropertyDatatype.STRING:
            return property_value
        elif property_type == PropertyDatatype.BOOLEAN:
            return str(property_value).lower()
        else:
            return property_value

    @deprecated
    def get_coerced_value(self) -> str:
        """
        Deprecated, use coerced_value()
        """
        return self.coerced_value()

    def id(self) -> str:
        """
        Returns the property's id
        """
        return self._id

    @deprecated
    def get_id(self) -> str:
        """
        Deprecated, user id()
        """
        return self.id()

    def datatype(self) -> str:
        """
        Returns the property's datatype.value
        """
        datatype = self._datatype
        logging.debug(f'reason=getDatatype,datatype={datatype}')
        return datatype

    @deprecated
    def get_datatype(self) -> str:
        """
        Deprecated, use datatype()
        """
        return self.datatype()

    def get_mqtt_client(self) -> MqttClient:
        """
        Who calls this function, and why?
        """
        node = self.get_node()
        if not node:
            logging.warning(f'reason=propertyGetMqttClientNoNode,propertyID={self._id}')
            return None
        mqttc = node.get_mqtt_client()
        if not mqttc:
            logging.warning(f'reason=propertyGetMqttClientNoMqttClient,propertyID={self._id}')
        return mqttc

    def start_mqtt_client(self) -> None:
        """
        Who calls this function, and why?
        """
        mqttc = self.get_mqtt_client()
        if not mqttc:
            logging.warning(f'reason=propertyStartMqttClientNoMqttClient,propertyID={self._id}')
            return
        try:
            if not mqttc.is_running:
                mqttc.start()
        except Exception as e:
            logging.warning(f'reason=propertyStartMqttClientException,e={e}')

    def settable(self) -> bool:
        return self._settable

    @deprecated
    def is_settable(self) -> bool:
        """
        Deprecated, use settable()
        """
        return self.settable()

    def retained(self) -> bool:
        return self._retained

    @deprecated
    def is_retained(self) -> bool:
        """
        Deprecated, use retained()
        """
        return self.retained()

    def is_json_datatype(self) -> bool:
        return self._datatype == PropertyDatatype.JSON

    def get_set_callback(self) -> Callable:
        return self._set_callback

    def supports_target(self) -> bool:
        """
        Returns supports_target
        """
        return self._supports_target

    def publish_target_value(self, payload) -> None:
        """
        The $target attribute must either be used for every value update (including the initial one), or it must never be used.
        TODO: Currently unimplemented, TBD how $target gets set on initial property value set...
        """
        logging.info(f'reason=propertyPublishTargetValue,propertyID={self._id},value={payload}')
        logging.warning(f'reason=propertyPublishTargetValueNotImplemented,propertyID={self._id},value={payload}')

    def publish_value(self) -> bool:
        """
        Publishes the property's value to Homie/eBus broker
        """
        mqttc = self.get_mqtt_client()
        if not mqttc or not mqttc.is_running:
            logging.warning(f'reason=propertyPublishValueNoMqttClient,id={self._id}')
            return False
        node_id = self.get_node_id()
        device_id = self.get_device_id()
        if not (device_id and node_id):
            logging.warning(f'propertyPublishValueInsufficientIDs,deviceID={device_id},nodeID={node_id},propertyID={self._id}')
            return False
        topic = f'{EBUS_HOMIE_DOMAIN}/{EBUS_HOMIE_VERSION_MAJOR}/{device_id}/{node_id}/{self._id}'
        if self._value is None:
            logging.debug(f'reason=propertyPublishValueIsNone,deviceID={device_id},nodeID={node_id},propertyID={self._id}')
            return False
        try:
            value = self.get_coerced_value()
            logging.debug(f'reason=propertyPublishValue,value={value},topic={topic},retained={self.retained()}')
            mqttc.publish(topic, value, retain=self.retained(), qos=EBUS_HOMIE_MQTT_QOS)
            return True
        except Exception as e:
            logging.warning(f'reason=propertyPublishValuePublishException,e={e}')
            return False

    def description(self) -> dict:
        """
        Returns a dict containing the Homie 5 $description of the Property
        """
        property = dict()
        property['name'] = self._name
        property['datatype'] = self.datatype()
        if self._format:
            property['format'] = self.format()
        if self._settable:
            property['settable'] = self._settable
        if not self._retained:
            property['retained'] = self._retained
        if self._unit:
            property['unit'] = self._unit
        return property

    def _settable_callback(self, topic: str, payload: Union[bytes,bytearray]) -> None:
        """
        For each settable property, there is a property/set topic that can be published to
        This is the callback for the subscription to each such property/set topic
        Examples:
        [homieDomain]/[homieVerson]/[deviceID]/[nodeID]/mode/set
        [homieDomain]/[homieVerion]/[deviceID]/[nodeID]/setpoint/set
        """
        logging.debug(f'reason=propertySetCallback,topic={topic}')
        try:
            topic_segments  = topic.split('/')
            homie_domain    = topic_segments[0]
            homie_version   = topic_segments[1]
            device_id       = topic_segments[2]
            node_id         = topic_segments[3]
            property_id     = topic_segments[4]
            property_id_set = topic_segments[5]
        except:
            logging.warning(f'reason=nodeSetCallbackTopicParseException,e={e}')
            return
        if not ((homie_domain == EBUS_HOMIE_DOMAIN) and
                (homie_version == str(EBUS_HOMIE_VERSION_MAJOR)) and
                (property_id_set == 'set')):
            logging.debug(f'reason=nodeSetCallbackInvalidTopic,topic={topic}')
            return
        # It is possible that we have a valid property/set
        set_callback = self.get_set_callback()
        if not self.settable():
            logging.info(f'reason=propertySetCallbackPropertyNotSettable,propertyID={property_id}')
            return
        if not set_callback:
            logging.info(f'reason=propertySetCallbackPropertyNoSetCallback,propertyID={property_id}')
            return
        try:
            decoded_payload = payload.decode('utf-8') # do we need to str() this?
            if self.is_json_datatype():
                payload = json.loads(decoded_payload)
            else:
                payload = decoded_payload
            # We have the payload
            logging.debug(f'reason=propertySetCallbackValue,propertyID={property_id},payload={payload},callback={set_callback}')
            if self.supports_target():
                # Property supports_target, publish that!
                self.publish_target_value(payload)
            # Call the property's set_callback function
            if self.async_loop:
                asyncio.ensure_future(set_callback(payload), loop=self.async_loop)
            else:
                set_callback(payload)
        except Exception as e:
            logging.exception(f'reason=propertySetCallbackException,e={e}')

    def set_subscribe(self) -> None:
        """
        Subscribe to property/set topic on Homie broker
        TODO: Not sure why this is a public method...
        """
        logging.debug(f'reason=propertySetSubscribe,id={self._id}')
        mqttc = self.get_mqtt_client()
        if not mqttc:
            logging.warning(f'reason=propertySetSubscribeNoMqttClient')
            return
        if not self.settable():
            logging.debug(f'reason=propertySetSubscribePropertyNotSettable,id={self._id}')
            return
        # Property is settable
        node_id = self.get_node_id()
        device_id = self.get_device_id()
        if not (device_id and node_id):
            logging.warning(f'propertySetSubscribeInsufficientIDs,deviceID={device_id},nodeID={node_id},propertyID={self._id}')
            return
        topic = f'{EBUS_HOMIE_DOMAIN}/{EBUS_HOMIE_VERSION_MAJOR}/{device_id}/{node_id}/{self._id}/set'
        try:
            mqttc.subscribe(topic, param=partial(self._settable_callback), qos=EBUS_HOMIE_MQTT_QOS)
        except Exception as e:
            logging.warning(f'reason=propertySetSubscribeSubscribeException,e={e}')
        # Start the MQTT client loop() thread
        # TODO: Is this the best, or even a good, place to do this???
        # self.start_mqtt_client()


class Node:
    """
    Object representing a Homie MQTT Node
    https://homieiot.github.io/specification/
    https://github.com/spanio/ebus-mqtt-convention/blob/main/CONVENTION.md
    """
    def __init__(self,
                 id: Optional[str] = None,
                 name: Optional[str] = None,
                 type: Optional[str] = None,
                 properties: dict = {},
                 device: Optional[Device] = None,
                 # mqttc: Optiona[MqttClient] = None, # DCJ pretty sure we can remove this
                 from_dict: Optional[dict] = None):
        """
        There are two ways to specify the arguments of a new Node:
          1. Explict named parameters
          2. Provide a dict whose keys are the parameter names
        These are mutually exclusive choices, if you specify from_dict, the parameters with are used
        """
        if from_dict:
            # Instantiating Node from dict
            self._id = from_dict.get('id', None)
            self._name = from_dict.get('name', self._id)
            self._type = from_dict.get('type', None)
            self._properties = from_dict.get('properties', {})
            self._device = from_dict.get('device', None)
        else:
            self._id = id
            if name:
                self._name = name
            else:
                self._name = id
            self._type = type
            self._properties = properties
            self._device = device

    def id(self) -> str:
        """
        Returns id of Node
        """
        return self._id

    @deprecated
    def get_id(self) -> str:
        """
        Deprecated, use id()
        """
        return self.id()

    def get_device_id(self) -> str:
        """
        Why is this a thing?
        """
        return self._device.id()

    def device(self) -> Device:
        return self._device

    @deprecated
    def get_device(self) -> Device:
        """
        Deprecated, use device()
        """
        return self.device()

    def set_device(self, device: Device) -> None:
        self._device = device

    def get_mqtt_client(self) -> MqttClient:
        device = self.get_device()
        if not device:
            logging.warning(f'reason=nodeGetMqttClientNoDevice,nodeID={self._id}')
            return None
        mqttc = device.get_mqtt_client()
        if not mqttc:
            logging.warning(f'reason=nodeGetMqttClientNoMqttClient,nodeID={self._id}')
        return mqttc

    @deprecated
    def new_property(self, id: str = None, name: Optional[str] = None) -> Property:
        """
        Deprecated, the main use was to get a property you could add_property_from_dict with
        Returns a new Property, with device and node set
        """
        if not name:
            name = id
        return Property(id=id, name=name, device=self.device(), node=self)

    def add_property(self, property: Property) -> Property:
        """
        Adds the property to properties, and returns property
        """
        if not property.get_node():
            property.set_node(self)
        # Note set_subscribe() checks if property is settable...
        property.set_subscribe()
        # TODO FIXME DCJ is property.publish_value() the right thing to do here?
        property.publish_value()
        self._properties.update({property.id(): property})
        return property

    def add_property_from_dict(self, property_dict: dict) -> Property:
        """
        Adds the property to properties, and returns property
        """
        return self.add_property(Property(from_dict=property_dict))

    def properties(self) -> dict:
        """
        Returns dict of Node's properties keyed by propertyID
        """
        return self._properties

    def get_properties(self) -> dict:
        """
        Returns dict of Node's properties keyed by propertyID
        """
        return self.properties()

    def description(self) -> dict:
        """
        Returns dict representing the Node's $description attribute
        """
        description = dict()
        description['name'] = self._name
        description['type'] = self._type
        properties = dict()
        for property_id, attributes in self._properties.items():
            properties[property_id] = attributes.description()
        description['properties'] = properties
        return description

    def publish(self) -> None:
        """
        Publishes Node, specifically its Properties to MQTT
        """
        for property in self._properties.values():
            property.publish_value()


class Device:
    """
    Object representing a Homie MQTT Device
    https://homieiot.github.io/specification/
    https://github.com/spanio/ebus-mqtt-convention/blob/main/CONVENTION.md
    TODO: Child devices might (or must?) use the root's MQTT client

    mqtt_cfg is a dict, two examples:

        {"host": "127.0.0.1",
         "port": 1885,
         "homie_domains": ["ebus"]}

        {"host": "mqtt.example.com",
         "port": 1883,
         "homie_domains": ["ebus"],
         "authentication": {"type": "USER_PASS",
	                    "username": "MyUserName",
                            "password": "SECRET"}}

    homie_domains config for future use, not currently supported by this code
    """
    def __init__(self,
                 id: str,
                 name: Optional[str] = None,
                 type: Optional[str] = None,
                 children_ids: Optional[List] = [],
                 root_id: Optional[str] = None,
                 parent_id: Optional[str] = None,
                 nodes: Optional[List] = [],
                 extensions: Optional[List] = [],
                 mqtt_cfg: Optional[dict] = {}):
        # Basic initialization
        self.mqttc = None
        self.state = None
        # Store the arguments
        self._id = id
        if name:
            self._name = name
        else:
            self._name = id
        self._type = type
        self._children_ids = children_ids
        self._mqtt_cfg = mqtt_cfg
        # If the device is NOT the root device, both root_id and parent_id are required
        if (root_id and not parent_id) or (not root_id and parent_id):
            logging.exception(f'reason=deviceInitRootParentException,id={id},rootID={root_id},parentID={parent_id}')
        self._root_id = root_id
        self._parent_id = parent_id
        # Initialize nodes here, but note that we'll add any provided nodes below
        self._nodes = {}
        self._extensions = extensions
        # Set the interesting/dynamic stuff
        # Distinguish between initial and subsequent connections to broker
        self.initial_broker_connection = True
        self.connect_broker()
        self.set_state(DeviceState.INIT)
        for node in nodes:
            self.add_node(node)
        self.update_description()

    @staticmethod
    def now_ems() -> int:
        """
        Returns current time as Epoch milliseconds
        """
        return round(time.time() * 1000)

    def id(self) -> str:
        """
        Returns id of Device
        """
        return self._id

    @deprecated
    def get_id(self) -> str:
        return self.id()

    def get_mqtt_client(self) -> MqttClient:
        mqttc = self.mqttc
        if not mqttc:
            logging.warning(f'reason=deviceGetMqttClientNoMqttClient,id={self._id}')
        return mqttc

    def start_mqtt_client(self) -> None:
        if not self.mqttc.is_running:
            self.mqttc.start()

    def description(self) -> dict:
        """
        Returns a dict of the $description attribute of the Device
        https://github.com/spanio/ebus-mqtt-convention/blob/main/CONVENTION.md
        """
        description = dict()
        description['homie'] = f'{EBUS_HOMIE_VERSION_MAJOR}.{EBUS_HOMIE_VERSION_MINOR}'
        # Version should be changed any time the description document is changed
        description['version'] = Device.now_ems()
        description['type'] = self._type
        description['name'] = self._name
        nodes_descriptions = dict()
        for node_id, node in self._nodes.items():
            nodes_descriptions[node_id] = node.description()
        description['nodes'] = nodes_descriptions
        description['children'] = self._children_ids
        if self._root_id:
            # ID of the root parent device.
            # Required if the device is NOT the root device, MUST be omitted otherwise.
            description['root'] = self._root_id
        if self._parent_id:
            # ID of the parent device.
            # Required if the parent is NOT the root device. Defaults to the value of the root property.
            description['parent'] = self._parent_id
        description['extensions'] = self._extensions
        return description

    def update_description(self) -> None:
        """
        Generates and sets the value of $description representing the eBus MQTT Device
        Publishes $description to broker
        https://github.com/spanio/ebus-mqtt-convention/blob/main/CONVENTION.md
        TODO: Remove this in lieu of publish_description()
        """
        self.publish_description()

    def set_state(self, state: DeviceState) -> bool:
        """
        Sets state, representing the $state attribute
        If the new state equals the existing state, noop, and returns False
        Returns True if state was set, and publishes $description to broker
        """
        if state != self.state:
            self.state = state
            self.publish_state()
            return True
        else:
            return False

    def add_child(self, child_id: str) -> bool:
        """
        Append child_id to children_ids
        Returns True if added, else False
        """
        if child_id in self._children_ids:
            return False
        else:
            self._children_ids.append(child_id)
            return True

    def remove_child(self, child_id: str) -> bool:
        """
        Remove child_id from children_ids, if it included
        Returns True if removed, else False
        """
        if child_id in self._children_ids:
            self._children_ids.remove(child_id)
            return True
        else:
            return False

    def set_parent(self, parent_id: str) -> None:
        """
        Sets parent_id
        """
        self._parent_id = parent_id

    def unset_parent(self) -> bool:
        """
        Unset parent_id if set
        Returns True if unset, else False
        """
        if self._parent_id:
            self._parent_id = None
            return True
        else:
            return False

    def new_node(self, id: str, name: str = None, type: str = None) -> Node:
        """
        Returns a new Node, with device and device_id set
        """
        return Node(id=id, name=name, type=type, device=self)

    def add_node(self, node: Node) -> Node:
        """
        Add node to nodes
        """
        if not node.get_device():
            node.set_device(self)
        node_id = node.get_id()
        self._nodes.update({node_id: node})
        node.publish()
        self.update_description()
        return node

    def add_node_from_dict(self, node_dict: dict) -> Node:
        """
        Create and add Node (as specified by node_dict), returns new Node
        """
        return self.add_node(Node(from_dict=node_dict))

    def remove_node(self, node_id: str) -> bool:
        """
        Removes node with node_id from nodes, if it exists
        Returns True if removed, else False
        """
        if node_id in self._nodes:
            self._nodes.pop(node_id, None)
            self.update_description()
            return True
        else:
            return False

    def publish(self, attribute: str = '', value: Optional[Any] = None) -> None:
        """
        Publishes the value argument to the device's attribute MQTT topic,
        or if the value is not provided, publishes the current (self) attribute value
        """
        if not self.mqttc:
            logging.info(f'reason=devicePublishNoMqttClient,attribute={attribute}')
            return
        if not self._id:
            logging.info(f'reason=devicePublishNoDeviceID')
            return
        try:
            base_topic = f'{EBUS_HOMIE_DOMAIN}/{EBUS_HOMIE_VERSION_MAJOR}/{self._id}/'
            if attribute == '$state':
                topic = base_topic + '$state'
                if value:
                    payload = value
                else:
                    payload = self.state
            elif attribute == '$description':
                topic = base_topic + '$description'
                if value:
                    payload = json.dumps(value)
                else:
                    payload = json.dumps(self.description())
            elif attribute == '$alert':
                topic = base_topic + '$alert'
                if value:
                    payload = value
                else:
                    logging.info(f'reason=devicePublishAlertNoValue,id={self._id}')
                    return
            self.mqttc.publish(topic, payload, retain=True, qos=EBUS_HOMIE_MQTT_QOS)
        except Exception as e:
            logging.warning(f'reason=devicePublishException,id={self._id},attribute={attribute},value={value},e={e}')

    def publish_state(self, state: Optional[DeviceState] = None) -> None:
        """
        Publishes the value of the state argument to the device's $state topic,
        or if state argument not provided, publishes value of self.state
        """
        if state:
            self.publish('$state', value=state)
        else:
            self.publish('$state', value=self.state)

    def publish_description(self, republish: bool = False) -> None:
        if republish:
            self.publish('$description')
        else:
            if self.state == DeviceState.READY:
                # Need to transition first to INIT
                self.publish_state(DeviceState.INIT)
                self.publish('$description')
                # Now that we've republished, restore $state to ready
                self.publish_state(DeviceState.READY)
            else:
                # TODO: should we be able to publish if DISCONNECTED, SLEEPING, or LOST?
                # If not in READY state, then we don't need to transition to INIT...
                logging.info(f'reason=publishDescriptionNotRepublishNotReady,state={self.state.name}')
                # Just publish description
                self.publish('$description')

    def publish_nodes(self) -> None:
        for node in self._nodes.values():
            node.publish()

    def on_connect(self) -> None:
        """
        This method will be called when the Homie/eBus client connects to the broker
        ATM the callback function signature has no arguments so use functools.partial to wrap this method
        Current intended use is to re-publish the Device's $state on connection (especially re-connection)
        """
        logging.info(f'reason=deviceOnConnectInvocation,initialBrokerConnection={self.initial_broker_connection}')
        if self.initial_broker_connection:
            self.initial_broker_connection = False
        else:
            self.publish_description(republish=True)
            self.publish_nodes()
            self.publish_state()

    def connect_broker(self) -> None:
        """
        Uses EBUS_BROKER_DEFAULT_ENDPOINT and EBUS_BROKER_DEFAULT_PORT if config file not specified
        TODO: If device is a child, likely this needs to happen on the device's root!
        """
        if self.mqttc:
            # If we already have a mqtt client, don't reconnect...
            return
        user_pass_valid = False
        client_id = self._id
        broker_endpoint = self._mqtt_cfg.get('host', EBUS_BROKER_DEFAULT_ENDPOINT)
        broker_port = self._mqtt_cfg.get('port', EBUS_BROKER_DEFAULT_PORT)
        broker_authentication = self._mqtt_cfg.get('authentication', {})
        authentication_type = broker_authentication.get('type', 'NONE')
        if authentication_type == USER_PASS_TYPE:
            username = broker_authentication.get('username', None)
            password = broker_authentication.get('password', None)
            user_pass_valid = username and password
        lwt = {'topic': f'{EBUS_HOMIE_DOMAIN}/{EBUS_HOMIE_VERSION_MAJOR}/{self._id}/$state',
               'payload': DeviceState.LOST.value}
        logging.info(f'reason=deviceConnectBroker,host={broker_endpoint},port={broker_port},authType={authentication_type},clientID={client_id}')
        try:
            if authentication_type == 'NONE':
                self.mqttc = MqttClient(client_id=client_id,
                                        endpoint=broker_endpoint,
                                        port=broker_port,
                                        lwt=lwt,
                                        on_connect_callback = partial(self.on_connect))
            elif (authentication_type == USER_PASS_TYPE) and user_pass_valid:
                self.mqttc = MqttClient(client_id=client_id,
                                        endpoint=broker_endpoint,
                                        port=broker_port,
                                        username=username,
                                        password=password,
                                        lwt=lwt,
                                        on_connect_callback = partial(self.on_connect))
            # TODO: Add additional authentication types here, e.g. certificate, OAuth2 token, etc.
        except Exception as e:
            logging.warning(f'reason=deviceConnectBrokerException,e={e}')

def ebus_cfg_add_auth(cfg, username, password):
    """
    Add authentication to the config dictionary
    """
    cfg['authentication'] = {
        "type": USER_PASS_TYPE,
        "username": username,
        "password": password
    }
    return cfg
