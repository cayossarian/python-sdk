"""Tests for the exported proxy/adapter helpers (adapter.py)."""

from ebus_sdk import (
    GroupedPropertyDict,
    ObservableProperty,
    bind_property_to_homie,
    set_homie_property_from_python_property,
)


class _FakeHomieProperty:
    """Stand-in for a homie.Property (only set_value is exercised)."""

    def __init__(self):
        self.value = None

    def set_value(self, v):
        self.value = v
        return True


def test_set_homie_property_from_python_property_copies_value():
    py = ObservableProperty(id="p", type=float, value=12.5)
    twin = _FakeHomieProperty()
    assert set_homie_property_from_python_property(twin, py) is True
    assert twin.value == 12.5


def test_bind_property_to_homie_mirrors_on_change():
    model = GroupedPropertyDict()
    model.create_group("meter")
    model.add_property("meter", ObservableProperty(id="active-power", type=float))
    twin = _FakeHomieProperty()

    bind_property_to_homie(model, "meter", "active-power", twin)
    model.set_value("meter", "active-power", 1850.0)
    assert twin.value == 1850.0


def test_bind_property_to_homie_does_not_fire_on_unchanged_value():
    model = GroupedPropertyDict()
    model.create_group("meter")
    model.add_property("meter", ObservableProperty(id="p", type=float, value=100.0))
    twin = _FakeHomieProperty()

    bind_property_to_homie(model, "meter", "p", twin)
    model.set_value("meter", "p", 100.0)  # unchanged -> no event -> twin untouched
    assert twin.value is None
    model.set_value("meter", "p", 200.0)  # changed -> mirrored
    assert twin.value == 200.0
