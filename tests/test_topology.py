"""Tests for the consumer-side site-topology assembler (SDK-644)."""

from __future__ import annotations

import json

from ebus_sdk import (
    CONNECTION_NODE_TYPE,
    DiscoveredDevice,
    SiteTopology,
)


def _dev(device_id, device_type, connection=None):
    """A synthetic DiscoveredDevice with an optional connection node + values."""
    d = DiscoveredDevice(device_id)
    nodes = {}
    if connection is not None:
        nodes["connection"] = {
            "type": CONNECTION_NODE_TYPE,
            "properties": {k: {"datatype": "string"} for k in connection},
        }
    d.update_description(json.dumps({"type": device_type, "nodes": nodes}))
    for k, v in (connection or {}).items():
        d.update_property("connection", k, str(v))
    return d


def _site():
    return [
        _dev("svc-lugs", "energy.ebus.device.lugs", {"service-rating": 200, "feeds-device-id": "mid-1"}),
        _dev("mid-1", "energy.ebus.device.mid", {"fed-by-device-id": "svc-lugs", "feeds-device-id": "panel"}),
        _dev("ft-lugs", "energy.ebus.device.lugs", {"fed-by-device-id": "mid-1"}),
        _dev("circuit-1", "energy.ebus.device.circuit", {"feeds-device-id": "bess", "backed-up": "BACKED_UP"}),
        _dev("circuit-2", "energy.ebus.device.circuit", {"feeds-device-id": "bess"}),
        _dev("circuit-3", "energy.ebus.device.circuit", {"feeds-role": "UNUSED"}),
        _dev("circuit-5", "energy.ebus.device.circuit", {"backed-up": "NOT_BACKED_UP"}),
        # "panel" and "bess" are referenced but NOT discovered (dangling).
    ]


def test_root_is_service_entrance_lugs():
    topo = SiteTopology.assemble(_site())
    assert topo.root() == ["svc-lugs"]


def test_connection_class_from_device_type():
    topo = SiteTopology.assemble(_site())
    n = topo.nodes()
    assert n["svc-lugs"].connection.connection_class == "main-lugs"  # lugs + service-rating
    assert n["ft-lugs"].connection.connection_class == "feedthrough-lugs"  # lugs, no rating
    assert n["mid-1"].connection.connection_class == "microgrid-interconnect"
    assert n["circuit-1"].connection.connection_class == "feeder-circuit"


def test_children_and_edge_reconciliation():
    topo = SiteTopology.assemble(_site())
    assert topo.children("svc-lugs") == ["mid-1"]
    edges = {(e.upstream_id, e.downstream_id): e for e in topo.edges()}
    # svc -> mid asserted by BOTH sides (svc feeds, mid fed-by): confirmed.
    assert edges[("svc-lugs", "mid-1")].confirmed is True
    # mid -> panel asserted by one side only.
    assert edges[("mid-1", "panel")].confirmed is False


def test_multi_source_upstream_and_aggregate():
    topo = SiteTopology.assemble(_site())
    # A multi-unit device fed by two circuits: both are upstream / feed it.
    assert topo.what_feeds("bess") == ["circuit-1", "circuit-2"]
    assert topo.connection_points_feeding("bess") == ["circuit-1", "circuit-2"]
    power = {"circuit-1": 1000.0, "circuit-2": 1500.0}
    assert topo.aggregate("bess", lambda cp: power.get(cp)) == 2500.0


def test_backed_up_loads():
    topo = SiteTopology.assemble(_site())
    assert topo.backed_up_loads() == ["circuit-1"]  # circuit-5 is NOT_BACKED_UP


def test_completeness_signal():
    topo = SiteTopology.assemble(_site())
    c = topo.completeness()
    # 7 connection points (svc, mid, ft-lugs, circuit-1/2/3/5): ft-lugs has fed-by
    # but no downstream fact, circuit-5 only backed-up, both "unknown" downstream.
    assert c["connection_points"] == 7
    assert c["unused"] == 1  # circuit-3
    assert c["unknown"] == 2  # ft-lugs, circuit-5 (no feeds-device-id / feeds-role)
    assert c["surveyed"] == 5


def test_dangling_references_become_undiscovered_placeholders():
    topo = SiteTopology.assemble(_site())
    assert topo.undiscovered() == ["bess", "panel"]
    assert topo.nodes()["bess"].discovered is False
    # The graph still resolves through them (never crashes on a dangling ref).
    # svc -> mid -> {panel (feeds), ft-lugs (which asserts fed-by=mid-1)}.
    assert topo.descendants("svc-lugs") == ["ft-lugs", "mid-1", "panel"]


def test_from_controller():
    class _Ctrl:
        def get_all_devices(self):
            return {d.device_id: d for d in _site()}

    topo = SiteTopology.from_controller(_Ctrl())
    assert topo.root() == ["svc-lugs"]


def test_cycle_is_traversed_safely():
    # A pathological retained state: A feeds B, B feeds A. Must not loop forever.
    devices = [
        _dev("a", "energy.ebus.device.circuit", {"feeds-device-id": "b"}),
        _dev("b", "energy.ebus.device.circuit", {"feeds-device-id": "a"}),
    ]
    topo = SiteTopology.assemble(devices)
    assert topo.descendants("a") == ["b"]  # terminates; excludes the start
    assert topo.descendants("b") == ["a"]


def test_devices_without_connection_node_are_plain_nodes():
    devices = [
        _dev("svc-lugs", "energy.ebus.device.lugs", {"service-rating": 200, "feeds-device-id": "panel"}),
        _dev("panel", "energy.ebus.device.distribution-enclosure"),  # discovered, no connection node
    ]
    topo = SiteTopology.assemble(devices)
    assert topo.nodes()["panel"].discovered is True
    assert topo.nodes()["panel"].connection is None
    assert topo.children("svc-lugs") == ["panel"]


def test_orphan_der_without_connection_record_is_unknown_not_dropped():
    # A DER discovered on the bus that owns NO connection record AND is referenced
    # by no other device's connection edge. SPAN connection records are
    # best-effort, so absence must read as "unknown" (a plain, edge-less node),
    # never as a topology assertion and never as a silent drop. (SDK-61t.5)
    devices = [
        _dev("svc-lugs", "energy.ebus.device.lugs", {"service-rating": 200, "feeds-device-id": "panel"}),
        _dev("panel", "energy.ebus.device.distribution-enclosure", {"fed-by-device-id": "svc-lugs"}),
        _dev("bess-orphan", "energy.ebus.device.bess"),  # discovered, no connection node, unreferenced
    ]
    topo = SiteTopology.assemble(devices)
    nodes = topo.nodes()
    # Present and discovered, with no connection assertion: never dropped.
    assert "bess-orphan" in nodes
    assert nodes["bess-orphan"].discovered is True
    assert nodes["bess-orphan"].connection is None
    # It was discovered, so it is not an undiscovered/dangling placeholder.
    assert "bess-orphan" not in topo.undiscovered()
    # Absence of a record reads as "unknown": no edges, not mistaken for a root
    # or a backed-up load, and every query is safe (empty, no crash).
    assert topo.what_feeds("bess-orphan") == []
    assert topo.children("bess-orphan") == []
    assert topo.descendants("bess-orphan") == []
    assert topo.root() == ["svc-lugs"]
    assert "bess-orphan" not in topo.backed_up_loads()
