import uuid
import logging
import re
from enum import Enum
from threading import Lock
from typing import List, Callable, Union, Optional, Any, Type, Dict

class ChangeEvent(Enum):
    """Events fired by GroupedPropertyDict"""
    GROUP_CREATED = 'group_created'
    GROUP_DELETED = 'group_deleted'
    PROPERTY_ADDED = 'property_added'
    PROPERTY_REMOVED = 'property_removed'
    PROPERTY_CHANGED = 'property_changed'
    BULK_UPDATE = 'bulk_update'

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
    """
    def __init__(self,
                 id: Optional[str] = None,
                 value: Any = None,
                 type: Union[Type, str, None] = None,
                 format: Optional[str] = None,
                 entity_setter: Optional[Callable] = None,
                 from_dict: dict = {}):
        self._lock = Lock()
        self._change_callbacks = {}
        self._set_callbacks = {}
        self._entity_setter = None
        if not from_dict:
            self._id = id
            self._value = value
            self._type = type
            self._format = format
            self._entity_setter = entity_setter
        else:
            self._id = from_dict.get('id')
            self._value = from_dict.get('value', None)
            self._type = from_dict.get('type', None)
            self._format = from_dict.get('format', None)
            self._entity_setter = from_dict.get('entity_setter', None)
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
                self._change_callbacks.pop(callback_id, None)
                return True
            elif callback_id in self._set_callbacks:
                self._set_callbacks.pop(callback_id, None)
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
                logging.warning(f'reason=setValueOnSetCallbackException,propertyId={self._id},newValue={new_value},callbackId={callback_id},e={e}')
        # Do we need to invoke on_change callbacks?
        if new_value != old_value:
            for callback_id, callback in on_change_callback_items:
                try:
                    callback(self)
                except Exception as e:
                    logging.warning(f'reason=setValueOnChangeCallbackException,propertyId={self._id},newValue={new_value},callbackId={callback_id}')
        return new_value

    def set_entity(self, new_value: Any) -> Any:
        """
        Used by client(s) to set the state of the entity the property represents,
        which is done by the property invoking a registered set_entity_callback function
        Presumably changing the entity state will eventually result in the property's state being set/changed,
        which will then invoke any on_set and/or in_change callbacks
        Returns the new value
        """
        if self._entity_setter:
            logging.info(f'reason=setEntity,propertyId={self._id},new_value={new_value}')
            try:
                self._entity_setter(new_value)
            except Exception as e:
                logging.warning(f'reason=setEntityException,propertyId={self._id},new_value={new_value},e={e}')
        else:
            logging.warning(f'reason=setEntityNoSetter,propertyId={self._id}')
        return new_value

    def set_entity_setter(self, entity_setter: Callable) -> None:
        """
        Registers an entity_setter function for the property
        """
        with self._lock:
            self._entity_setter = entity_setter

    def as_dict(self) -> dict:
        return vars(self)

class BulkUpdateContext:
    """Context manager for bulk updates to GroupedPropertyDict"""
    def __init__(self, grouped_dict):
        self.grouped_dict = grouped_dict
        self.pending_events = []

    def __enter__(self):
        self.grouped_dict._bulk_mode = True
        self.grouped_dict._bulk_context = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.grouped_dict._bulk_mode = False
        self.grouped_dict._bulk_context = None
        if not exc_type and self.pending_events:
            # Fire single BULK_UPDATE event with all changes
            for observer_id, callback in self.grouped_dict._observers.items():
                try:
                    callback(ChangeEvent.BULK_UPDATE, changes=self.pending_events)
                except Exception as e:
                    logging.warning(f'reason=bulkUpdateObserverException,observerId={observer_id},e={e}')
        return False

    def add_event(self, event_type: ChangeEvent, **kwargs):
        """Add event to pending list"""
        event = {'event_type': event_type}
        event.update(kwargs)
        self.pending_events.append(event)

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
        self._observers = {}
        self._bulk_mode = False
        self._bulk_context = None

    def value(self, group: str, id: str) -> Any:
        """
        Returns the value of PythonProperty group.id
        TODO: Is the lock even needed here?
        """
        # get is thread-safe for this class, and value() is thread-safe for PythonProperty
        property = self.get(group, id)
        if property:
            return property.value()
        else:
            return None

    def type(self, group: str, id: str) -> Any:
        """
        Returns the type of PythonProperty group.id
        """
        # get is thread-safe for this class, and type() is thread-safe for PythonProperty
        property = self.get(group, id)
        if property:
            return property.type()
        else:
            return None

    def format(self, group: str, id: str) -> Any:
        """
        Returns the format of PythonProperty group.id
        """
        # get is thread-safe for this class, and format() is thread-safe for PythonProperty
        property = self.get(group, id)
        if property:
            return property.format()
        else:
            return None

    def get(self, group: str, id: str) -> Optional[PythonProperty]:
        """
        Returns the PythonProperty group.id
        """
        with self._lock:
            if group not in self._properties:
                logging.debug(f'reason=groupedPropertiesGetNoGroupByThatName,group={group},id={id}')
                return None
            if id not in self._properties[group]:
                logging.debug(f'reason=groupedPropertiesGetNoPythonPropertyByThatId,group={group},id={id}')
                return None
            return self._properties[group][id]

    def create_group(self, group_name: str) -> None:
        """Explicitly create a new group"""
        with self._lock:
            if group_name in self._properties:
                logging.warning(f'reason=groupAlreadyExists,groupName={group_name}')
                return
            self._properties[group_name] = {}
        self._fire_event(ChangeEvent.GROUP_CREATED, group_name=group_name)

    def delete_group(self, group_name: str) -> None:
        """Delete a group and all its properties"""
        with self._lock:
            if group_name not in self._properties:
                logging.warning(f'reason=deleteGroupNotFound,groupName={group_name}')
                return
            del self._properties[group_name]
        self._fire_event(ChangeEvent.GROUP_DELETED, group_name=group_name)

    def delete_property(self, group: str, property_id: str) -> None:
        """Delete a specific property from a group"""
        with self._lock:
            if group not in self._properties:
                logging.warning(f'reason=deletePropertyGroupNotFound,group={group},propertyId={property_id}')
                return
            if property_id not in self._properties[group]:
                logging.warning(f'reason=deletePropertyNotFound,group={group},propertyId={property_id}')
                return
            del self._properties[group][property_id]
        self._fire_event(ChangeEvent.PROPERTY_REMOVED, group_name=group, property_id=property_id)

    def group_exists(self, group_name: str) -> bool:
        """Check if a group exists"""
        with self._lock:
            return group_name in self._properties

    def add_property(self, group: str, property: PythonProperty) -> PythonProperty:
        """
        Adds PythonProperty to the group, returns the PythonProperty
        """
        # id() thread-safe for PythonProperty
        property_id = property.id()
        group_created = False
        with self._lock:
            if group not in self._properties:
                # Group doesn't exist, create it first
                self._properties[group] = {}
                group_created = True
            self._properties[group][property_id] = property
        # Fire events outside the lock to avoid deadlock
        if group_created:
            self._fire_event(ChangeEvent.GROUP_CREATED, group_name=group)
        self._fire_event(ChangeEvent.PROPERTY_ADDED, group_name=group, property_id=property_id, property=property)
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
                logging.warning(f'reason=groupedPropertiesSetValueNoGroupByThatName,group={group},id={id},value={value}')
            else:
                logging.warning(f'reason=groupedPropertiesSetValueNoPythonPropertyByThatId,group={group},id={id},value={value}')
            return None
        old_value = this_property.value()
        result = this_property.set_value(value)
        if old_value != value:
            self._fire_event(ChangeEvent.PROPERTY_CHANGED,
                             group_name=group,
                             property_id=id,
                             property=this_property,
                             old_value=old_value,
                             new_value=value)
        return result

    def set_entity(self, group: str, id: str, value: Any) -> Any:
        """
        Calls the entity_setter of the PythonProperty group.id
        Returns the new value
        """
        # TODO change .info to .debug
        logging.info(f'reason=groupedProperties,group={group},id={id},value={value}')
        # get() is thread-safe for the GroupedPropertyDict, and set_entity() is thread-safe for PythonProperty?
        this_property = self.get(group, id)
        if not this_property:
            # groups() is thread-safe for GroupedPropertyDict
            if group not in self.groups():
                logging.warning(f'reason=groupedPropertiesSetEntityNoGroupByThatName,group={group},id={id},value={value}')
            else:
                logging.warning(f'reason=groupedPropertiesSetEntityNoPythonPropertyByThatId,group={group},id={id},value={value}')
        return this_property.set_entity(value)

    def set_entity_setter(self, group: str, id: str, callback: Callable) -> None:
        """
        Sets entity_setter on the PythonProperty group.id
        """
        # get() is thread-safe for the GroupedPropertyDict, and set_entity_setter() is thread-safe for PythonProperty?
        this_property = self.get(group, id)
        if not this_property:
            logging.warning(f'reason=groupedPropertiesSetEntitySetterNoPropertyByThatId,group={group},id={id}')
            return None
        return this_property.set_entity_setter(callback)

    def groups(self) -> List:
        """
        Returns a list of groups
        """
        with self._lock:
            return list(self._properties.keys())

    def items(self, group: str) -> List:
        """
        Returns a list containing tuples of each (id, PythonProperty) in the group
        """
        with self._lock:
            return self._properties[group].items()

    def as_dict(self) -> dict:
        returned_dict = {}
        for group in self.groups():
            group_dict = {}
            for id, property in self.items(group):
                group_dict.update({id:  property.as_dict()})
            returned_dict.update({group: group_dict})
        return returned_dict

    def add_observer(self, callback: Callable, event_types: List[ChangeEvent] = None,
                    group_filter: str = None) -> uuid.UUID:
        """
        Register an observer for changes
        callback signature: callback(event_type: ChangeEvent, **kwargs)
        Returns observer_id for later removal
        """
        observer_id = uuid.uuid1()
        with self._lock:
            self._observers[observer_id] = callback
        logging.info(f'reason=observerRegistered,observerId={observer_id}')
        return observer_id

    def remove_observer(self, observer_id: uuid.UUID) -> bool:
        """Remove an observer"""
        with self._lock:
            if observer_id in self._observers:
                del self._observers[observer_id]
                logging.info(f'reason=observerRemoved,observerId={observer_id}')
                return True
            return False

    def bulk_update(self) -> BulkUpdateContext:
        """Return a context manager for bulk updates"""
        return BulkUpdateContext(self)

    def _fire_event(self, event_type: ChangeEvent, **kwargs):
        """Fire an event to all observers"""
        if self._bulk_mode and self._bulk_context:
            # In bulk mode, accumulate events
            self._bulk_context.add_event(event_type, **kwargs)
        else:
            # Fire immediately
            with self._lock:
                observers = list(self._observers.items())
            for observer_id, callback in observers:
                try:
                    callback(event_type, **kwargs)
                except Exception as e:
                    logging.warning(f'reason=observerCallbackException,observerId={observer_id},eventType={event_type},e={e}')

    def get_groups_by_pattern(self, pattern: str) -> List[str]:
        """Return groups matching regex pattern"""
        with self._lock:
            return [g for g in self._properties.keys() if re.match(pattern, g)]

    def has_group(self, group_name: str) -> bool:
        """Check if specific group exists"""
        with self._lock:
            return group_name in self._properties

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
            return list(self._properties.keys())
