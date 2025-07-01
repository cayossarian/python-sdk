import uuid
import logging # TODO FIXME?
from threading import Lock
from typing import List, Callable, Union, Optional, Any, Type

class PythonProperty():
    """
    A PythonProperty is modeled (very) loosely on a Homie Property
    (https://homieiot.github.io/specification/)
    A PythonProperty has a:
      id - string
      value - Any
      type - A Python type, or a string representing Homie types that are not native Python types (i.e. JSON)
      format - string, tells you something more about the value, see Homie
    You say: Gee that seems like a variable...
    Correct-a-mundo, BUT a PythonProperty supports a (list of) callback functions,
      which are called/invoked whenever the PythonProperty's value is set to a changed value
    Note also that a PythonProperty's type is explictly defined, a feature Python variables doesn't provide
    "format" is stolen vertbatim from Homie, used here most often to provide clients with the list of possible ENUM values,
      which is valuable/useful because often the client doesn't have access to the enum itself (due to scoping)
    All methods/operations are intended to be thread-safe, please file an issue if you beleive otherwise
    FUTURE/TODO:
      Should callback should be called with the PythonProperty, not its value?
      Should callback be called even if the PythonProperty's value doesn’t change when set?
    """
    def __init__(self,
                 id: Optional[str] = None,
                 value: Any = None,
                 type: Union[Type, str, None] = None,
                 format: Optional[str] = None,
                 from_dict: dict = {}):
        self._lock = Lock()
        self._change_callbacks = {}
        self._set_callbacks = {}
        if not from_dict:
            self._id = id
            self._value = value
            self._type = type
            self._format = format
        else:
            self._id = from_dict.get('id')
            self._value = from_dict.get('value', None)
            self._type = from_dict.get('type', None)
            self._format = from_dict.get('format', None)
        if not self._id:
            # Specifying the id is required!
            logging.warning(f'reason=pythonPropertyInitNoIdSpecified!')
            # TODO, throw an exception?

    def id(self) -> str:
        """
        Returns id of the PythonProperty
        """
        with self._lock:
            return self._id

    def value(self) -> Any:
        """
        Returns value of the PythonProperty
        """
        with self._lock:
            return self._value

    def type(self) -> Any:
        """
        Returns type of the PythonProperty
        """
        with self._lock:
            return self._type

    def format(self) -> Any:
        """
        Returns format of the PythonProperty
        """
        with self._lock:
            return self._format

    def add_on_change_callback(self, callback: Callable) -> uuid.UUID:
        """
        Adds callback to the "list" of callbacks that will be called when the PythonProperty is set to a changed value
        Returns a callback_id, (a uuid1) that can be used subsequently to remove the callback
        A callback is a function of one argument, the PythonProperty
        """
        callback_id = uuid.uuid1()
        with self._lock:
            self._change_callbacks.update({callback_id: callback})
        return callback_id

    def add_on_set_callback(self, callback: Callable) -> uuid.UUID:
        """
        Adds callback to the "list" of callbacks that will be called when the PythonProperty is set, even if that set doesn't change the value
        Returns a callback_id, (a uuid1) that can be used subsequently to remove the callback
        A callback is a function of one argument, the PythonProperty
        """
        callback_id = uuid.uuid1()
        with self._lock:
            self._set_callbacks.update({callback_id: callback})
        return callback_id

    def remove_callback(self, callback_id: uuid.UUID) -> bool:
        """
        Removes the callback associated with callback_id from the "list" of callbacks
        Returns True if successful, False if callback_id not found
        """
        with self._lock:
            if callback_id in self._change_callbacks:
                self._change_callbacks.pop(calback_id, None)
                return True
            elif callback_id in self._set_callbacks:
                self._set_callbacks.pop(calback_id, None)
                return True
            else:
                logging.warning(f'removeCallbackNoSuchId,callbackId={callback_id},propertyId={self._id}')

    def set_value(self, new_value: Any) -> Any:
        """
        Sets the value of the PythonProperty
        Invokes all on_set callbacks
        If the value changes as a result of this method, then invoke all on_change callbacks
        Returns the new value
        """
        old_value = self._value
        with self._lock:
            self._value = new_value
            on_change_callback_items = self._change_callbacks.items()
            on_set_callback_items = self._set_callbacks.items()
            # We have mutated the value, and obtained all the callbacks, so OK to release lock?
        # Invoke on_set callbacks
        for callback_id, callback in on_set_callback_items:
            try:
                callback(self)
            except Exception as e:
                logging.warning(f'reason=setValueException,propertyId={self._id},callbackId={callback_id}')
        # Do we need to invoke on_change callbacks?
        if new_value != old_value:
            for callback_id, callback in on_change_callback_items:
                try:
                    callback(self)
                except Exception as e:
                    logging.warning(f'reason=setValueException,propertyId={self._id},callbackId={callback_id}')
        return new_value


class GroupedPropertyDict():
    """
    GroupedPropertyDict is a dict of dicts of PythonProperty instances,
    keyed first by group, and second by property.id
    In practice, this is a good way to deal with "a bunch of PythonPropertys",
      and to "group" collections of PythonPropertys
    All methods/operations are intended to be thread-safe, please file an issue if you beleive otherwise
      Thread-safety is implementation is (attempted?) to rely on both the GroupedPropertyDict itself AND
        the thread-safety of the underlying PythonProperty method calls.
    """

    def __init__(self):
        self._lock = Lock()
        self._properties = {}

    def get(self, group: str, id: str) -> Optional[dict]:
        """
        Returns the PythonProperty group.id
        """
        with self._lock:
            group_dict = self._properties.get(group, {})
            # OK to release lock now?
        if not group_dict:
            logging.warning(f'reason=groupedPropertiesGetGroupNotFound,group={group},id:{id}')
        return group_dict.get(id, None)

    def value(self, group: str, id: str) -> Any:
        """
        Returns the value of the PythonProperty group.id
        """
        # get() is thread-safe for the GroupedPropertyDict, and value() is thread-safe for PythonProperty?
        property = self.get(group, id)
        return property.value()

    def type(self, group: str, id: str) -> Any:
        """
        Returns the type of the PythonProperty group.id
        """
        # get() is thread-safe for the GroupedPropertyDict, and type() is thread-safe for PythonProperty?
        property = self.get(group, id)
        return property.type()

    def format(self, group: str, id: str) -> Any:
        """
        Returns the format of the PythonProperty group.id
        """
        # get() is thread-safe for the GroupedPropertyDict, and format() is thread-safe for PythonProperty?
        property = self.get(group, id)
        return property.format()

    def add_property(self, group: str, property) -> PythonProperty:
        """
        Adds PythonProperty to the group, returns the PythonProperty
        """
        # id() thread-safe for PythonProperty
        property_id = property.id()
        with self._lock:
            if group not in self._properties:
                # Group doesn't exist, to add it first
                self._properties.update({group: {}})
            self._properties[group][property_id] = property
            return property

    def add_property_from_dict(self, group: str, property_dict: dict = {}) -> PythonProperty:
        """
        Creates a PythonProperty from property_dict, and adds the PythonProperty to the group
        Returns the created PythonProperty
        """
        property = PythonProperty(from_dict=property_dict)
        # add_property() itself is thread-safe itself, so no lock needed here?
        return self.add_property(group, property)

    def add_property_on_change_callback(self, group: str, id: str, callback: Callable) -> uuid.UUID:
        """
        Adds callback to the list of callbacks that will be called when the PythonProperty group.id is set to a changed value
        Returns a callback_id, (a uuid1) that can be used subsequently to remove the callback
        """
        # get() is thread-safe for the GroupedPropertyDict, and add_on_change_callback() is thread-safe for PythonProperty?
        this_property = self.get(group, id)
        if not this_property:
            logging.warning(f'reason=groupedPropertiesAddPythonPropertyOnChangeCallbackNoPropertyByThatId,group={group},id={id}')
            return None
        return this_property.add_on_change_callback(callback)

    def add_property_on_set_callback(self, group: str, id: str, callback: Callable) -> uuid.UUID:
        """
        Adds callback to the list of callbacks that will be called when the PythonProperty group.id is set
        Returns a callback_id, (a uuid1) that can be used subsequently to remove the callback
        """
        # get() is thread-safe for the GroupedPropertyDict, and add_on_set_callback() is thread-safe for PythonProperty?
        this_property = self.get(group, id)
        if not this_property:
            logging.warning(f'reason=groupedPropertiesAddPythonPropertyOnSetCallbackNoPropertyByThatId,group={group},id={id}')
            return None
        return this_property.add_on_set_callback(callback)

    def remove_property_callback(self, group: str, id: str, callback_id: uuid.UUID) -> bool:
        """
        Removes the callback associated with callback_id from the "list" of callbacks for PythonProperty group.id
        Returns True if successful, False if callback_id not found
        """
        # get() is thread-safe for the GroupedPropertyDict, and remove_callback() is thread-safe for PythonProperty?
        this_property = self.get(group, id)
        if not this_property:
            logging.warning(f'reason=groupedPropertiesRemovePythonPropertyCallbackNoPropertyByThatId,group={group},id={id}')
            return False
        return this_property.remove_callback(callback_id)

    def set_value(self, group: str, id: str, value: Any) -> Any:
        """
        Sets the value of the PythonProperty group.id
        Returns the new value
        """
        # get() is thread-safe for the GroupedPropertyDict, and set_value() is thread-safe for PythonProperty?
        this_property = self.get(group, id)
        if not this_property:
            # groups() is thread-safe for GroupedPropertyDict
            if group not in self.groups():
                logging.warning(f'reason=groupedPropertiesSetNoGroupByThatName,group={group},id={id},value={value}')
            else:
                logging.warning(f'reason=groupedPropertiesSetNoPythonPropertyByThatId,group={group},id={id},value={value}')
        return this_property.set_value(value)

    def groups(self) -> List:
        """
        Returns a list of groups
        """
        with self._lock:
            return self._properties.keys()

    def items(self, group: str) -> List:
        """
        Returns a list containing tuples of each (id, PythonProperty) in the group
        """
        with self._lock:
            return self._properties[group].items()


class PropertyDict():
    """
    PropertyDict is a dict of PythonProperty objects keyed by property.id
    All methods/operations are intended to be thread-safe, please file an issue if you beleive otherwise
      Thread-safety is implementation is (attempted?) to rely on both the PropertyDict itself AND
        the thread-safety of the underlying PythonProperty method calls.
    TODO: add_property_from_dict(), add_property_on_change_callback(), remove_property_callback()
    I originally thought this was going to be useful, but GroupedPropertyDict was far superior,
      so I never ended up using this...
    """

    def __init__(self):
        self._lock = Lock()
        self._properties = {}

    def get(self, id: str) -> Optional[dict]:
        """
        Returns the PythonProperty with id
        """
        with self._lock:
            if id not in self._properties:
                logging.warning(f'reason=propertiesDictNoPropertyByThatId,id={id}')
            return self._properties.get(id, None)

    def add_property(self, property) -> PythonProperty:
        """
        Adds PythonProperty to the dict, returns the PythonProperty
        """
        # id() is thread-safe for a PythonProperty
        property_id = property.id()
        with self._lock:
            self._properties.update({property_id: property})
        return property

    def set_value(self, id: str, value: Any) -> Any:
        """
        Sets the value of the PropertyDict id
        Returns the new value
        """
        # get() is thread-safe for PropertyDict
        this_property = self.get(id)
        if not this_property:
            logging.warning(f'reason=setPropertyValueNoPropertyByThatId,id={id},value={value}')
            return None
        # set_value() is thread-safe for PythonProperty
        return this_property.set_value(value)

    def items(self) -> List:
        """
        Returns a list containing tuples of each (id, PythonProperty) in the PropertyDict
        """
        with self._lock:
            return self._properties.items()

    def ids(self) -> List:
        """
        Returns a list containing id of each PythonProperty in the PropertyDict
        """
        with self._lock:
            return self._properties.keys()
