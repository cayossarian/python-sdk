"""Tests for the declarative PropertySpec + build_from_declarations builder."""

from ebus_sdk import (
    Device,
    GroupedPropertyDict,
    PropertyDatatype,
    PropertySpec,
    Unit,
    build_from_declarations,
    python_type_for,
)


def test_python_type_for():
    assert python_type_for(PropertyDatatype.FLOAT) is float
    assert python_type_for(PropertyDatatype.INTEGER) is int
    assert python_type_for(PropertyDatatype.STRING) is str
    assert python_type_for(PropertyDatatype.BOOLEAN) is bool


def test_build_from_declarations_creates_nodes_props_and_binds(mock_paho):
    device = Device("dev-1", type="energy.ebus.device.submeter", mqtt_cfg={"host": "localhost", "port": 1883})
    device.start_mqtt_client()
    model = GroupedPropertyDict()

    specs = [
        PropertySpec("info", "serial-number", PropertyDatatype.STRING),
        PropertySpec("meter", "active-power", PropertyDatatype.FLOAT, Unit.WATT),
        PropertySpec("meter", "imported-energy", PropertyDatatype.FLOAT, Unit.WATT_HOUR, scale=1000.0),
    ]
    homie_props = build_from_declarations(
        device,
        model,
        specs,
        values={("meter", "active-power"): 1850.0, ("info", "serial-number"): "abc"},
    )

    # One Homie node per capability, default eBus capability node type.
    assert device.get_node("info") is not None
    assert device.get_node("meter") is not None
    assert device.get_node("meter").type() == "energy.ebus.capability.meter"

    # The observable model holds the properties, grouped by capability, seeded.
    assert model.value("meter", "active-power") == 1850.0
    assert model.value("info", "serial-number") == "abc"

    # Returned Homie twins are bound: a later model change mirrors onto Homie.
    assert set(homie_props) == {
        ("info", "serial-number"),
        ("meter", "active-power"),
        ("meter", "imported-energy"),
    }
    assert homie_props[("meter", "active-power")].value() == 1850.0
    model.set_value("meter", "active-power", 2000.0)
    assert homie_props[("meter", "active-power")].value() == 2000.0


def test_build_from_declarations_custom_node_type(mock_paho):
    device = Device("dev-2", mqtt_cfg={"host": "localhost", "port": 1883})
    device.start_mqtt_client()
    model = GroupedPropertyDict()
    build_from_declarations(
        device,
        model,
        [PropertySpec("sensors", "temp", PropertyDatatype.FLOAT, Unit.DEGREE_CELSIUS)],
        node_type=lambda cap: "sensor",
    )
    assert device.get_node("sensors").type() == "sensor"
