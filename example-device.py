#!/usr/bin/env python3

# This may be removed in Python 3.10+.
from __future__ import annotations

import argparse
import json
import logging
import os
import collections
import threading
import sys
import uuid
import homie as homie
from homie import EBUS_HOMIE_DOMAIN, EBUS_HOMIE_VERSION_MAJOR, DeviceState, PropertyDatatype, Unit
from functools import cached_property, partial, reduce
from typing import List, Dict, Union, Callable, Optional, Any
from enum import Enum
# Workaround due to non-support of StrEnum in current Gen2 FW Python, StrEnum available in enum, remove
from mqtt import MqttClient
from property import PythonProperty, GroupedPropertyDict
# TODO debug only, remove
from pprint import pp, pformat


class ExampleDevice():

    def __init__(self,
                 logger: Optional[logging.Logger] = None):
        # Set up logger
        if logger is None:
            # Create a default logger
            logger = logging.getLogger(self.__class__.__name__)
            logger.setLevel(logging.INFO)
            # Prevent duplicate handlers if multiple instances are created
            if not logger.handlers:
                handler = logging.StreamHandler()
                formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
                handler.setFormatter(formatter)
                logger.addHandler(handler)
        self._logger = logger
        # End of logger setup
        self._props = GroupedPropertyDict()
        self.add_environment_properties()

    def add_environment_properties(self):
        """
        Adds all the properties, under the group "environment"
        """
        properties = [
            {'id': 'temperature', 'type': float, 'value': 42.0},
            {'id': 'air-pressure', 'type': float, 'value': 24.0},
            {'id': 'humidity', 'type': float, 'value': 50.0}]
        for property_dict in properties:
            self._props.add_property_from_dict('sensors', property_dict)

    def get_property(self, group: str, property_id: str) -> PythonProperty:
        return self._props.get(group, property_id)

    def add_property_on_change_callback(self, group: str, id: str, callback: Callable) -> uuid.UUID:
        """
        Adds a callback to the PythonProperty group.id, that will be called when the PythonProperty's value changes
        Returns a callback_id (a uuid1) that can be used subsequently to remove the callback
        """
        return self._props.add_property_on_change_callback(group, id, callback)

    def as_dict(self) -> dict:
        return self._props.as_dict()


def set_homie_property_from_python_property(homie_property: homie.Property,
                                            python_property: PythonProperty) -> bool:
    """
    Used as a callback for a PythonProperty
    The PythonProperty's callback returns the PythonProperty,
    and this function sets the homie.Property's value to the value of the PythonProperty
    """
    return homie_property.set_value(python_property.value())


class ExampleDeviceAdapter():

    def __init__(self,
                 example_device: Any,
                 logger: Optional[logging.Logger] = None):
        # Set up logger
        if logger is None:
            # Create a default logger
            logger = logging.getLogger(self.__class__.__name__)
            logger.setLevel(logging.INFO)
            # Prevent duplicate handlers if multiple instances are created
            if not logger.handlers:
                handler = logging.StreamHandler()
                formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
                handler.setFormatter(formatter)
                logger.addHandler(handler)
        self._logger = logger
        # End of logger setup
        self._example_device = example_device
        self.create_device_and_nodes()

    def as_dict(self) -> dict:
        return self._example_device.as_dict()

    @staticmethod
    def ebus_broker_cfg() -> Dict:
        """
        Returns eBus MQTT broker configuration as a dict
        """
        # If EBUS_BROKER_CFG is not defined in the environment, use hardcoded default
        mqtt_cfg_file = os.getenv('EBUS_BROKER_CFG', 'broker-cfg.json')
        try:
            with open(mqtt_cfg_file, 'r') as file:
                mqtt_auth = json.load(file)
                return mqtt_auth
        except Exception as e:
            logging.warning(f'reason=ebusBrokerCfgException,file={mqtt_cfg_file},e={e}')
            return {}

    def create_homie_device(self) -> homie.Device:
        try:
            homie_device = homie.Device('33A3D78A3D78', # TODO: get this from somewhere
                                        name='Thing1',
                                        type='energy.ebus.device.esp32',
                                        mqtt_cfg=ExampleDeviceAdapter.ebus_broker_cfg())
        except Exception as e:
            self._logger.warning(f'reason=createHomieDeviceException,e={e}')

        homie_device.start_mqtt_client()
        return homie_device

    def add_homie_environment_node(self) -> homie.Node:
        properties = {
            'temperature': {
                'name': 'Temperature',
                'datatype': homie.PropertyDatatype.FLOAT,
                'unit': homie.Unit.DEGREE_CELSIUS,
                'round_to': 1,
                'group': 'sensors',
                'py_property_id': 'temperature'},
            'air-pressure': {
                'name': 'Air Pressure',
                'datatype': homie.PropertyDatatype.FLOAT,
                'unit': homie.Unit.PASCAL,
                'round_to': 1,
                'group': 'sensors',
                'py_property_id': 'air-pressure'},
            'humidity': {
                'name': 'Humidty',
                'datatype': homie.PropertyDatatype.FLOAT,
                'unit': homie.Unit.PERCENT,
                'round_to': 1,
                'group': 'sensors',
                'py_property_id': 'humidity'}
            }
        homie_node = self._homie_device.add_node_from_dict(
            {'id': 'environment',
             'name': 'Temperature, Pressure, and Humidty Sensors',
             'type': 'energy.ebus.device.esp32.sensors'})
        for homie_property_id, mapping_dict in properties.items():
            group = mapping_dict.get('group', 'sensors')
            py_property_id = mapping_dict.get('py_property_id', None)
            self._logger.debug(f'reason=addHomieEnvironment,pyPropertyId={py_property_id}')
            self._logger.info(f'reason=addHomieEnvironmentNode,group={group},pyPropertyId={py_property_id}')
            property = self._example_device.get_property(group, py_property_id)
            property_type = property.type()
            homie_datatype_from_property = None
            if property_type:
                homie_datatype_from_property = homie.datatype_from_type(property_type)
            datatype = mapping_dict.get('datatype', homie_datatype_from_property)
            round_to = mapping_dict.get('round_to', None)
            format = mapping_dict.get('format', None)
            unit = mapping_dict.get('unit', None)
            property_dict = {'id': homie_property_id,
                             'value': property.value(),
                             'round_to': round_to}
            if datatype:
                property_dict.update({'datatype': datatype})
            if format:
                property_dict.update({'format': format})
            if unit:
                property_dict.update({'unit': unit})
            homie_property = homie_node.add_property_from_dict(property_dict)
            self._example_device.add_property_on_change_callback(group,
                                                                 py_property_id,
                                                                 partial(set_homie_property_from_python_property,
                                                                         homie_property))
        return homie_node

    def create_device_and_nodes(self):
        self._homie_device = self.create_homie_device()
        self.add_homie_environment_node()


def main():
    # Set up logging
    logging.basicConfig()
    example_device_logger = logging.getLogger('ExampleDevice')
    example_device_logger.setLevel(logging.INFO)
    example_device_adapter_logger = logging.getLogger('ExampleDeviceAdapter')
    example_device_adapter_logger.setLevel(logging.INFO)

    example_device = ExampleDevice(logger=example_device_logger)
    example_device_logger.info(f'\n{pformat(example_device.as_dict())}')
    example_device_adapter = ExampleDeviceAdapter(example_device, logger=example_device_adapter_logger)
    example_device_adapter_logger.info(f'\n{pformat(example_device_adapter.as_dict())}')
    # Wait forever for event that will never come
    event = threading.Event()
    event.wait()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # https://stackoverflow.com/a/7073293
        sys.exit(0)
    except Exception as e:
        logging.exception(e)
