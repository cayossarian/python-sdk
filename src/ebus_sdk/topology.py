"""Consumer-side site-topology assembler for the eBus ``connection`` capability.

The eBus specification records site topology as a DISTRIBUTED set of per-device
wiring edges (the ``connection`` capability: ``feeds-*`` / ``fed-by-*`` and
attributes), with no central topology authority. A consumer reconstructs the
site graph by following those references across devices; this module implements
the "Assembling the site topology" algorithm from
``capabilities/connection.md`` once, so every consumer (dashboard, EMS,
coordinator) gets a resolved, queryable graph instead of re-crawling the bus.

It is deliberately a VIEW, not a source of truth: it is built from whatever
edges are currently retained on the broker, so partial data yields a partial
graph, two consumers with different partial data may assemble slightly different
graphs, and dangling references or cycles degrade gracefully (marked, never a
crash). Consumer-side and READ-ONLY: publishers emit only their own edges, and
this module performs no commissioning writes and no live incremental
re-assembly (rebuild on demand).

Reconciliation rule (both directions are MAY and may be one-sided or disagree):
an edge exists if EITHER side asserts it (``A``'s ``feeds-device-id = B`` or
``B``'s ``fed-by-device-id = A``); an edge asserted from both sides is
``confirmed``, one asserted from a single side is not. Multiple upstreams for one
device are not a conflict but the legitimate multi-source case (e.g. a multi-unit
BESS whose units land on separate circuits).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# The connection capability's node type and property ids (capabilities/connection.md).
CONNECTION_NODE_TYPE = "energy.ebus.capability.connection"

# Device-type suffix -> connection-point class. `lugs` splits on service-rating
# (present => the service-entrance root; absent => a downstream feedthrough).
_CLASS_BY_TYPE_SUFFIX = {
    ".circuit": "feeder-circuit",
    ".mid": "microgrid-interconnect",
}


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


@dataclass
class ConnectionRecord:
    """One device's ``connection`` edges and attributes, as published."""

    device_id: str
    device_type: Optional[str] = None
    connection_class: Optional[str] = None
    feeds_device_id: Optional[str] = None
    feeds_device_type: Optional[str] = None
    feeds_device_status: Optional[str] = None
    fed_by_device_id: Optional[str] = None
    fed_by_device_type: Optional[str] = None
    fed_by_device_status: Optional[str] = None
    backed_up: Optional[str] = None
    feeds_role: Optional[str] = None
    service_rating: Optional[int] = None
    overcurrent_protection: Optional[int] = None
    count: Optional[int] = None

    @property
    def is_service_entrance(self) -> bool:
        """True for the service-entrance connection point (upstream lugs with a rating)."""
        return self.service_rating is not None and self.connection_class == "main-lugs"


@dataclass
class TopologyNode:
    """A device in the assembled graph. ``discovered`` is False for a node that
    only appears as the target of a dangling reference (never itself seen)."""

    device_id: str
    device_type: Optional[str] = None
    connection: Optional[ConnectionRecord] = None
    discovered: bool = True


@dataclass
class TopologyEdge:
    """A directed upstream -> downstream wiring edge, with which side(s) asserted it."""

    upstream_id: str
    downstream_id: str
    sources: set = field(default_factory=set)  # e.g. {"circuit-1:feeds", "bess-1:fed-by"}

    @property
    def confirmed(self) -> bool:
        """True when both the upstream's ``feeds`` and the downstream's ``fed-by`` agree."""
        return any(s.endswith(":feeds") for s in self.sources) and any(s.endswith(":fed-by") for s in self.sources)


def _connection_class(device_type: Optional[str], service_rating: Optional[int]) -> Optional[str]:
    if not device_type:
        return None
    for suffix, cls in _CLASS_BY_TYPE_SUFFIX.items():
        if device_type.endswith(suffix):
            return cls
    if device_type.endswith(".lugs"):
        return "main-lugs" if service_rating is not None else "feedthrough-lugs"
    return None


def _find_connection_node(description: dict) -> Optional[str]:
    """Return the id of the device's ``connection``-typed node, if any."""
    nodes = description.get("nodes") if isinstance(description, dict) else None
    if not isinstance(nodes, dict):
        return None
    for node_id, node in nodes.items():
        if isinstance(node, dict) and node.get("type") == CONNECTION_NODE_TYPE:
            return node_id
    return None


def _read_record(device) -> Optional[ConnectionRecord]:
    """Build a ConnectionRecord from a discovered device, or None if it owns no connection node."""
    description = getattr(device, "description", None) or {}
    device_type = description.get("type") if isinstance(description, dict) else None
    node_id = _find_connection_node(description)
    if node_id is None:
        return None

    def val(prop_id: str) -> Any:
        return device.get_property(node_id, prop_id)

    service_rating = _coerce_int(val("service-rating"))
    return ConnectionRecord(
        device_id=device.device_id,
        device_type=device_type,
        connection_class=_connection_class(device_type, service_rating),
        feeds_device_id=val("feeds-device-id"),
        feeds_device_type=val("feeds-device-type"),
        feeds_device_status=val("feeds-device-status"),
        fed_by_device_id=val("fed-by-device-id"),
        fed_by_device_type=val("fed-by-device-type"),
        fed_by_device_status=val("fed-by-device-status"),
        backed_up=val("backed-up"),
        feeds_role=val("feeds-role"),
        service_rating=service_rating,
        overcurrent_protection=_coerce_int(val("overcurrent-protection")),
        count=_coerce_int(val("count")),
    )


class SiteTopology:
    """The assembled site graph: nodes (devices) + directed upstream->downstream edges.

    Build it with :meth:`assemble` (from an iterable of discovered devices) or
    :meth:`from_controller`. All queries are read-only and cycle-safe.
    """

    def __init__(self, records: Dict[str, ConnectionRecord], device_types: Dict[str, Optional[str]]):
        self._nodes: Dict[str, TopologyNode] = {}
        self._edges: Dict[tuple, TopologyEdge] = {}
        # adjacency
        self._downstream: Dict[str, set] = {}
        self._upstream: Dict[str, set] = {}

        for device_id, device_type in device_types.items():
            self._ensure_node(device_id, device_type, discovered=True)
        for device_id, record in records.items():
            self._nodes[device_id].connection = record
            if record.feeds_device_id:
                self._add_edge(device_id, record.feeds_device_id, f"{device_id}:feeds")
            if record.fed_by_device_id:
                self._add_edge(record.fed_by_device_id, device_id, f"{device_id}:fed-by")

    # -- construction --------------------------------------------------------

    @classmethod
    def assemble(cls, devices) -> "SiteTopology":
        """Assemble a topology from an iterable of discovered devices (objects with
        ``device_id``, ``description``, and ``get_property(node, prop)``)."""
        records: Dict[str, ConnectionRecord] = {}
        device_types: Dict[str, Optional[str]] = {}
        for device in devices:
            description = getattr(device, "description", None) or {}
            device_types[device.device_id] = description.get("type") if isinstance(description, dict) else None
            record = _read_record(device)
            if record is not None:
                records[device.device_id] = record
        return cls(records, device_types)

    @classmethod
    def from_controller(cls, controller) -> "SiteTopology":
        """Assemble from a Controller's currently-discovered devices."""
        return cls.assemble(controller.get_all_devices().values())

    def _ensure_node(self, device_id: str, device_type: Optional[str] = None, discovered: bool = True) -> TopologyNode:
        node = self._nodes.get(device_id)
        if node is None:
            node = TopologyNode(device_id=device_id, device_type=device_type, discovered=discovered)
            self._nodes[device_id] = node
        elif device_type and node.device_type is None:
            node.device_type = device_type
        return node

    def _add_edge(self, upstream_id: str, downstream_id: str, source: str) -> None:
        # A referenced device may not have been discovered: create a placeholder.
        self._ensure_node(upstream_id, discovered=upstream_id in self._nodes and self._nodes[upstream_id].discovered)
        self._ensure_node(
            downstream_id, discovered=downstream_id in self._nodes and self._nodes[downstream_id].discovered
        )
        edge = self._edges.get((upstream_id, downstream_id))
        if edge is None:
            edge = TopologyEdge(upstream_id=upstream_id, downstream_id=downstream_id)
            self._edges[(upstream_id, downstream_id)] = edge
        edge.sources.add(source)
        self._downstream.setdefault(upstream_id, set()).add(downstream_id)
        self._upstream.setdefault(downstream_id, set()).add(upstream_id)

    # -- structure -----------------------------------------------------------

    def nodes(self) -> Dict[str, TopologyNode]:
        """All graph nodes keyed by device id (including dangling-reference placeholders)."""
        return dict(self._nodes)

    def edges(self) -> List[TopologyEdge]:
        """All directed upstream -> downstream edges."""
        return list(self._edges.values())

    def undiscovered(self) -> List[str]:
        """Device ids referenced by an edge but never themselves discovered (dangling)."""
        return sorted(nid for nid, n in self._nodes.items() if not n.discovered)

    # -- queries -------------------------------------------------------------

    def root(self) -> List[str]:
        """Service-entrance device(s): the upstream ``lugs`` carrying ``service-rating``.

        Falls back to devices with no upstream edge when no service-entrance is
        published (a partial graph), so traversal always has an anchor.
        """
        roots = [nid for nid, n in self._nodes.items() if n.connection and n.connection.is_service_entrance]
        if roots:
            return sorted(roots)
        return sorted(nid for nid in self._nodes if not self._upstream.get(nid))

    def parents(self, device_id: str) -> List[str]:
        """Devices wired directly upstream of ``device_id``."""
        return sorted(self._upstream.get(device_id, set()))

    def children(self, device_id: str) -> List[str]:
        """Devices wired directly downstream of ``device_id``."""
        return sorted(self._downstream.get(device_id, set()))

    # `what_feeds` reads naturally: the devices that feed into this one.
    what_feeds = parents

    def connection_points_feeding(self, device_id: str) -> List[str]:
        """Connection-owning devices whose ``feeds-device-id`` targets ``device_id``.

        The "many connection points behind one device" case (e.g. the circuits a
        multi-unit BESS lands on); sum a metric across these to get the device's
        total flow (see :meth:`aggregate`).
        """
        return sorted(
            nid for nid, n in self._nodes.items() if n.connection and n.connection.feeds_device_id == device_id
        )

    def aggregate(self, device_id: str, value_fn: Callable[[str], Optional[float]]) -> float:
        """Sum ``value_fn`` over the connection points feeding ``device_id``.

        ``value_fn`` maps a connection-point device id to a number (e.g. its
        measured power) or None to skip; the topology stays decoupled from any
        metric source, so the caller supplies the reader. Returns the sum.
        """
        total = 0.0
        for cp in self.connection_points_feeding(device_id):
            v = value_fn(cp)
            if v is not None:
                total += v
        return total

    def backed_up_loads(self) -> List[str]:
        """Connection points on the backup (island) side (``backed-up == BACKED_UP``)."""
        return sorted(nid for nid, n in self._nodes.items() if n.connection and n.connection.backed_up == "BACKED_UP")

    def ancestors(self, device_id: str) -> List[str]:
        """All devices upstream of ``device_id`` toward the root (cycle-safe)."""
        return self._walk(device_id, self._upstream)

    def descendants(self, device_id: str) -> List[str]:
        """All devices downstream of ``device_id`` (cycle-safe)."""
        return self._walk(device_id, self._downstream)

    def _walk(self, start: str, adjacency: Dict[str, set]) -> List[str]:
        seen: set = set()
        stack = list(adjacency.get(start, set()))
        while stack:
            nid = stack.pop()
            if nid in seen or nid == start:
                continue
            seen.add(nid)
            stack.extend(adjacency.get(nid, set()))
        return sorted(seen)

    def completeness(self) -> Dict[str, int]:
        """A survey-completeness signal over the connection points.

        ``surveyed`` records a positive downstream fact (a ``feeds-device-id`` or a
        ``feeds-role``, including ``UNUSED`` = "surveyed, nothing connected");
        ``unknown`` connection points publish neither (absence = unknown, which
        ``feeds-role = UNUSED`` deliberately distinguishes from empty).
        """
        points = [n.connection for n in self._nodes.values() if n.connection]
        surveyed = sum(1 for c in points if c.feeds_device_id or c.feeds_role)
        unused = sum(1 for c in points if c.feeds_role == "UNUSED")
        return {
            "connection_points": len(points),
            "surveyed": surveyed,
            "unknown": len(points) - surveyed,
            "unused": unused,
        }
