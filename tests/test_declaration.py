"""Tests for the declarative PropertySpec + build_from_declarations builder."""

from ebus_sdk import (
    Device,
    GroupedPropertyDict,
    PropertyDatatype,
    PropertySpec,
    Unit,
    build_from_declarations,
    python_type_for,
    resolve,
    specs_and_values,
)

_MAP = {
    "kWh_Tot": PropertySpec("meter", "imported-energy", PropertyDatatype.FLOAT, Unit.WATT_HOUR, scale=1000.0),
    "RMS_Watts_Tot": PropertySpec("meter", "active-power", PropertyDatatype.FLOAT, Unit.WATT),
}


def test_resolve_explicit_mapping_applies_scale():
    fields = {"kWh_Tot": 22.5, "RMS_Watts_Tot": 719}
    resolved = {r.spec.prop_id: r for r in resolve(fields.keys(), fields, _MAP)}
    assert resolved["imported-energy"].value == 22500.0  # kWh -> Wh
    assert resolved["active-power"].value == 719


def test_resolve_fallback_fills_gaps_and_holds_unmapped():
    fields = {"kWh_Tot": 1.0, "vendor_temp": 21.5, "mystery": 9}
    fb_spec = PropertySpec("meter", "vendor-temp", PropertyDatatype.FLOAT, Unit.DEGREE_CELSIUS)
    resolved = resolve(fields.keys(), fields, _MAP, fallback=lambda f: fb_spec if f == "vendor_temp" else None)
    prop_ids = {r.spec.prop_id for r in resolved}
    assert prop_ids == {"imported-energy", "vendor-temp"}  # explicit + fallback; "mystery" held
    assert len(resolved) == 2


def test_resolve_declared_without_value_yields_none():
    resolved = resolve(["kWh_Tot"], {}, _MAP)
    assert len(resolved) == 1 and resolved[0].value is None


def test_specs_and_values_split_omits_none():
    resolved = resolve(["kWh_Tot", "RMS_Watts_Tot"], {"kWh_Tot": 2.0}, _MAP)
    specs, values = specs_and_values(resolved)
    assert len(specs) == 2  # both declared
    assert values == {("meter", "imported-energy"): 2000.0}  # only the observed one, scaled


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
