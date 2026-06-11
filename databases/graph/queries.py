# TASK 5 — Neo4j Query Functions
# Compatible with Task 4 graph design:
#   (:MetroStation), (:NationalRailStation)
#   [:METRO_LINK], [:RAIL_LINK], [:INTERCHANGE_TO]

from __future__ import annotations

from typing import Any
from neo4j import GraphDatabase

from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


VALID_NETWORKS = {"auto", "metro", "rail"}
VALID_FARE_CLASSES = {"standard", "first"}


def _driver():
    """Return a Neo4j driver. Caller is responsible for closing."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def _normalise_network(network: str | None) -> str:
    value = (network or "auto").lower()
    if value not in VALID_NETWORKS:
        return "auto"
    return value


def _normalise_fare_class(fare_class: str | None) -> str:
    value = (fare_class or "standard").lower()
    if value not in VALID_FARE_CLASSES:
        return "standard"
    return value


def _node_network(node: Any) -> str:
    labels = set(node.labels)
    if "MetroStation" in labels:
        return "metro"
    if "NationalRailStation" in labels:
        return "rail"
    return node.get("network", "unknown")


def _parse_path(path_obj) -> tuple[list[dict], list[dict]]:
    """Parse a Neo4j path into station dictionaries and relationship-leg dictionaries."""
    stations: list[dict] = []
    for node in path_obj.nodes:
        stations.append(
            {
                "station_id": node.get("station_id"),
                "name": node.get("name"),
                "network": _node_network(node),
                "lines": node.get("lines", []),
                "zone": node.get("zone"),
                "closed": node.get("closed", False),
            }
        )

    legs: list[dict] = []
    for rel in path_obj.relationships:
        legs.append(
            {
                "type": rel.type,
                "line": rel.get("line"),
                "service_type": rel.get("service_type"),
                "travel_time_min": rel.get("travel_time_min"),
                "fare_standard": rel.get("fare_standard"),
                "fare_first": rel.get("fare_first"),
            }
        )

    return stations, legs


def _route_dict(
    *,
    found: bool,
    origin_id: str,
    destination_id: str,
    metric_name: str,
    metric_value: int | float | None,
    path_obj=None,
) -> dict:
    if not found or path_obj is None:
        return {
            "found": False,
            "origin_id": origin_id,
            "destination_id": destination_id,
            metric_name: None,
            "path": [],
            "legs": [],
        }

    stations, legs = _parse_path(path_obj)
    return {
        "found": True,
        "origin_id": origin_id,
        "destination_id": destination_id,
        metric_name: metric_value,
        "path": stations,
        "legs": legs,
    }


# ── 1. FASTEST / SHORTEST ROUTE ──────────────────────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """
    Return the route with the lowest total travel_time_min.

    Required return shape:
      dict with path list + total_time_min numeric metric.
    """
    network = _normalise_network(network)

    if origin_id == destination_id:
        return {
            "found": True,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "total_time_min": 0,
            "path": [{"station_id": origin_id}],
            "legs": [],
        }

    query = """
    MATCH (start {station_id: $origin_id})
    MATCH (end {station_id: $destination_id})
    WHERE (start:MetroStation OR start:NationalRailStation)
      AND (end:MetroStation OR end:NationalRailStation)
      AND coalesce(start.closed, false) = false
      AND coalesce(end.closed, false) = false
    MATCH path = (start)-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..15]->(end)
    WHERE NONE(n IN nodes(path) WHERE coalesce(n.closed, false) = true)
      AND (
        $network = 'auto'
        OR ($network = 'metro' AND ALL(n IN nodes(path) WHERE n:MetroStation))
        OR ($network = 'rail' AND ALL(n IN nodes(path) WHERE n:NationalRailStation))
      )
    WITH path,
         reduce(total = 0, r IN relationships(path) |
             total + toInteger(coalesce(r.travel_time_min, 0))
         ) AS total_time_min
    RETURN path, total_time_min
    ORDER BY total_time_min ASC, length(path) ASC
    LIMIT 1
    """

    with _driver() as driver:
        with driver.session() as session:
            record = session.run(
                query,
                origin_id=origin_id,
                destination_id=destination_id,
                network=network,
            ).single()

    if not record:
        return _route_dict(
            found=False,
            origin_id=origin_id,
            destination_id=destination_id,
            metric_name="total_time_min",
            metric_value=None,
        )

    return _route_dict(
        found=True,
        origin_id=origin_id,
        destination_id=destination_id,
        metric_name="total_time_min",
        metric_value=int(record["total_time_min"]),
        path_obj=record["path"],
    )


# ── 2. CHEAPEST ROUTE ────────────────────────────────────────────────────────

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """
    Return the route with the lowest edge-weighted fare.

    fare_class visibly changes edge weights:
      - standard uses fare_standard
      - first uses fare_first when available, otherwise fare_standard
    """
    network = _normalise_network(network)
    fare_class = _normalise_fare_class(fare_class)

    if origin_id == destination_id:
        return {
            "found": True,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "fare_class": fare_class,
            "total_fare_usd": 0.0,
            "path": [{"station_id": origin_id}],
            "legs": [],
        }

    query = """
    MATCH (start {station_id: $origin_id})
    MATCH (end {station_id: $destination_id})
    WHERE (start:MetroStation OR start:NationalRailStation)
      AND (end:MetroStation OR end:NationalRailStation)
      AND coalesce(start.closed, false) = false
      AND coalesce(end.closed, false) = false
    MATCH path = (start)-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..15]->(end)
    WHERE NONE(n IN nodes(path) WHERE coalesce(n.closed, false) = true)
      AND (
        $network = 'auto'
        OR ($network = 'metro' AND ALL(n IN nodes(path) WHERE n:MetroStation))
        OR ($network = 'rail' AND ALL(n IN nodes(path) WHERE n:NationalRailStation))
      )
    WITH path,
         reduce(total = 0.0, r IN relationships(path) |
             total +
             CASE
               WHEN $fare_class = 'first'
                    AND r.fare_first IS NOT NULL
               THEN toFloat(r.fare_first)
               ELSE toFloat(coalesce(r.fare_standard, 0.0))
             END
         ) AS total_fare_usd,
         reduce(total = 0, r IN relationships(path) |
             total + toInteger(coalesce(r.travel_time_min, 0))
         ) AS total_time_min
    RETURN path, total_fare_usd, total_time_min
    ORDER BY total_fare_usd ASC, total_time_min ASC, length(path) ASC
    LIMIT 1
    """

    with _driver() as driver:
        with driver.session() as session:
            record = session.run(
                query,
                origin_id=origin_id,
                destination_id=destination_id,
                network=network,
                fare_class=fare_class,
            ).single()

    if not record:
        return _route_dict(
            found=False,
            origin_id=origin_id,
            destination_id=destination_id,
            metric_name="total_fare_usd",
            metric_value=None,
        ) | {"fare_class": fare_class}

    stations, legs = _parse_path(record["path"])
    return {
        "found": True,
        "origin_id": origin_id,
        "destination_id": destination_id,
        "fare_class": fare_class,
        "total_fare_usd": round(float(record["total_fare_usd"]), 2),
        "total_time_min": int(record["total_time_min"]),
        "path": stations,
        "legs": legs,
    }


# ── 3. ALTERNATIVE ROUTES AVOIDING ONE STATION ───────────────────────────────

def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[dict]:
    """
    Return up to max_routes paths that exclude avoid_station_id.
    Each result includes path list and total_time_min metric.
    """
    network = _normalise_network(network)
    max_routes = max(1, int(max_routes))

    query = """
    MATCH (start {station_id: $origin_id})
    MATCH (end {station_id: $destination_id})
    WHERE (start:MetroStation OR start:NationalRailStation)
      AND (end:MetroStation OR end:NationalRailStation)
      AND coalesce(start.closed, false) = false
      AND coalesce(end.closed, false) = false
    MATCH path = (start)-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..15]->(end)
    WHERE NONE(n IN nodes(path) WHERE n.station_id = $avoid_station_id)
      AND NONE(n IN nodes(path) WHERE coalesce(n.closed, false) = true)
      AND (
        $network = 'auto'
        OR ($network = 'metro' AND ALL(n IN nodes(path) WHERE n:MetroStation))
        OR ($network = 'rail' AND ALL(n IN nodes(path) WHERE n:NationalRailStation))
      )
    WITH path,
         reduce(total = 0, r IN relationships(path) |
             total + toInteger(coalesce(r.travel_time_min, 0))
         ) AS total_time_min
    RETURN path, total_time_min
    ORDER BY total_time_min ASC, length(path) ASC
    LIMIT $max_routes
    """

    routes: list[dict] = []
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                query,
                origin_id=origin_id,
                destination_id=destination_id,
                avoid_station_id=avoid_station_id,
                network=network,
                max_routes=max_routes,
            )
            for record in result:
                stations, legs = _parse_path(record["path"])
                routes.append(
                    {
                        "origin_id": origin_id,
                        "destination_id": destination_id,
                        "avoid_station_id": avoid_station_id,
                        "total_time_min": int(record["total_time_min"]),
                        "path": stations,
                        "legs": legs,
                    }
                )

    return routes


# ── 4. CROSS-NETWORK INTERCHANGE PATH ────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    Return a cross-network path that must traverse INTERCHANGE_TO and must contain
    both MetroStation and NationalRailStation nodes.
    """
    query = """
    MATCH (start {station_id: $origin_id})
    MATCH (end {station_id: $destination_id})
    WHERE (start:MetroStation OR start:NationalRailStation)
      AND (end:MetroStation OR end:NationalRailStation)
      AND start <> end
      AND coalesce(start.closed, false) = false
      AND coalesce(end.closed, false) = false
    MATCH path = (start)-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..15]->(end)
    WHERE ANY(r IN relationships(path) WHERE type(r) = 'INTERCHANGE_TO')
      AND ANY(n IN nodes(path) WHERE n:MetroStation)
      AND ANY(n IN nodes(path) WHERE n:NationalRailStation)
      AND NONE(n IN nodes(path) WHERE coalesce(n.closed, false) = true)
    WITH path,
         reduce(total = 0, r IN relationships(path) |
             total + toInteger(coalesce(r.travel_time_min, 0))
         ) AS total_time_min,
         reduce(cost = 0.0, r IN relationships(path) |
             cost + toFloat(coalesce(r.fare_standard, 0.0))
         ) AS total_fare_usd
    RETURN path, total_time_min, total_fare_usd
    ORDER BY total_time_min ASC, length(path) ASC
    LIMIT 1
    """

    with _driver() as driver:
        with driver.session() as session:
            record = session.run(
                query,
                origin_id=origin_id,
                destination_id=destination_id,
            ).single()

    if not record:
        return {
            "found": False,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "total_time_min": None,
            "path": [],
            "legs": [],
            "interchange_points": [],
        }

    stations, legs = _parse_path(record["path"])
    interchange_points = []
    for index, leg in enumerate(legs):
        if leg["type"] == "INTERCHANGE_TO":
            interchange_points.append(
                {
                    "from": stations[index]["station_id"],
                    "to": stations[index + 1]["station_id"],
                }
            )

    return {
        "found": True,
        "origin_id": origin_id,
        "destination_id": destination_id,
        "total_time_min": int(record["total_time_min"]),
        "total_fare_usd": round(float(record["total_fare_usd"]), 2),
        "path": stations,
        "legs": legs,
        "interchange_points": interchange_points,
        "networks_in_path": sorted({station["network"] for station in stations}),
    }


# ── 5. DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """
    Return all stations within N hops of delayed_station_id.
    Each result includes hops_away and count.
    """
    safe_hops = max(1, min(int(hops), 10))

    query = f"""
    MATCH (start {{station_id: $delayed_station_id}})
    WHERE start:MetroStation OR start:NationalRailStation
    MATCH p = (start)-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..{safe_hops}]-(other)
    WHERE (other:MetroStation OR other:NationalRailStation)
      AND start <> other
      AND coalesce(other.closed, false) = false
      AND NONE(n IN nodes(p) WHERE coalesce(n.closed, false) = true AND n <> start)
    WITH other,
         min(length(p)) AS hops_away,
         count(p) AS path_count
    RETURN other.station_id AS station_id,
           other.name AS name,
           CASE WHEN other:MetroStation THEN 'metro' ELSE 'rail' END AS network,
           other.lines AS lines_affected,
           hops_away,
           path_count AS count
    ORDER BY hops_away ASC, station_id ASC
    """

    with _driver() as driver:
        with driver.session() as session:
            result = session.run(query, delayed_station_id=delayed_station_id)
            return [dict(record) for record in result]


# ── 6. DIRECT STATION CONNECTIONS ────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """
    Return direct neighbours with travel_time_min per neighbour.
    Uses undirected matching so incoming and outgoing links are both visible.
    """
    query = """
    MATCH (start {station_id: $station_id})-[r:METRO_LINK|RAIL_LINK|INTERCHANGE_TO]-(other)
    WHERE (start:MetroStation OR start:NationalRailStation)
      AND (other:MetroStation OR other:NationalRailStation)
      AND coalesce(other.closed, false) = false
    RETURN DISTINCT other.station_id AS station_id,
           other.name AS name,
           CASE WHEN other:MetroStation THEN 'metro' ELSE 'rail' END AS network,
           type(r) AS relationship_type,
           r.line AS line,
           r.service_type AS service_type,
           toInteger(coalesce(r.travel_time_min, 0)) AS travel_time_min,
           r.fare_standard AS fare_standard,
           r.fare_first AS fare_first
    ORDER BY travel_time_min ASC, station_id ASC
    """

    with _driver() as driver:
        with driver.session() as session:
            result = session.run(query, station_id=station_id)
            return [dict(record) for record in result]
