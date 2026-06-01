from __future__ import annotations

from typing import Optional
from neo4j import GraphDatabase

from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

def _driver():
    """Return a Neo4j driver. Caller is responsible for closing."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def _parse_path(path_record):
    """Helper to parse a Neo4j path object into a usable dict."""
    nodes = path_record.nodes
    rels = path_record.relationships
    
    stations = [
        {
            "station_id": n.get("station_id"),
            "name": n.get("name"),
            "network": n.get("network"),
            "lines": n.get("lines", [])
        }
        for n in nodes
    ]
    legs = []
    
    for r in rels:
        legs.append({
            "type": r.type,
            "line": r.get("line"),
            "service_type": r.get("service_type"),
            "travel_time_min": r.get("travel_time_min"),
            "fare_standard": r.get("fare_standard"),
            "fare_first": r.get("fare_first")
        })
        
    return stations, legs


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(origin_id: str, destination_id: str, network: str = "auto") -> dict:
    if origin_id == destination_id:
        return {
            "found": True,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "total_time_min": 0,
            "path": [{"station_id": origin_id}],
            "legs": []
        }
    query = """
    MATCH (start:Station {station_id: $origin_id})
    MATCH (end:Station {station_id: $destination_id})
    CALL apoc.algo.dijkstra(start, end, 'CONNECTS_TO|INTERCHANGE_TO', 'travel_time_min', 0.0) YIELD path, weight
    RETURN path, weight AS total_time_min
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(query, origin_id=origin_id, destination_id=destination_id)
            record = result.single()
            if not record:
                return {"found": False}
                
            stations, legs = _parse_path(record["path"])
            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": record["total_time_min"],
                "path": stations,
                "legs": legs
            }


# ── CHEAPEST ROUTE (Dijkstra by fare) ────────────────────────────────────────

def query_cheapest_route(origin_id: str, destination_id: str, network: str = "auto", fare_class: str = "standard") -> dict:
    weight_prop = "fare_first" if fare_class == "first" else "fare_standard"
    if origin_id == destination_id:
        return {
            "found": True,
            "total_fare_usd": 0,
            "stations": [{"station_id": origin_id}],
            "legs": []
        }
    query = """
    MATCH (start:Station {station_id: $origin_id})
    MATCH (end:Station {station_id: $destination_id})
    CALL apoc.algo.dijkstra(start, end, 'CONNECTS_TO|INTERCHANGE_TO', $weight_prop, 0.0) YIELD path, weight
    RETURN path, weight AS total_fare_usd
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(query, origin_id=origin_id, destination_id=destination_id, weight_prop=weight_prop)
            record = result.single()
            if not record:
                return {"found": False}
                
            stations, legs = _parse_path(record["path"])
            return {
                "found": True,
                "total_fare_usd": record["total_fare_usd"],
                "stations": stations,
                "legs": legs
            }


# ── ALTERNATIVE ROUTES (avoiding a station) ───────────────────────────────────

def query_alternative_routes(origin_id: str, destination_id: str, avoid_station_id: str, network: str = "auto", max_routes: int = 3) -> list[dict]:
    query = """
    MATCH (start:Station {station_id: $origin_id})
    MATCH (end:Station {station_id: $destination_id})
    MATCH path = (start)-[:CONNECTS_TO|INTERCHANGE_TO*1..8]->(end)
    WHERE NONE(n IN nodes(path) WHERE n.station_id = $avoid_station_id)
    RETURN path
    ORDER BY reduce(s=0, r IN relationships(path) | s + coalesce(r.travel_time_min, 0)) ASC
    LIMIT $max_routes
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(query, origin_id=origin_id, destination_id=destination_id, avoid_station_id=avoid_station_id, max_routes=max_routes)
            routes = []
            for record in result:
                stations, legs = _parse_path(record["path"])
                routes.append({
                    "stations": stations,
                    "legs": legs
                })
            return routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    # 核心修正：加入 WHERE start <> end 預防起終點相同導致 shortestPath 報錯
    query = """
    MATCH (start:Station {station_id: $origin_id})
    MATCH (end:Station {station_id: $destination_id})
    WHERE start <> end
    MATCH path = shortestPath((start)-[:CONNECTS_TO|INTERCHANGE_TO*]->(end))
    WHERE any(r IN relationships(path) WHERE type(r) = 'INTERCHANGE_TO')
    RETURN path, reduce(s=0, r IN relationships(path) | s + coalesce(r.travel_time_min, 0)) AS total_time_min
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(query, origin_id=origin_id, destination_id=destination_id)
            record = result.single()
            if not record:
                return {"found": False}
                
            stations, legs = _parse_path(record["path"])
            
            interchange_points = []
            for i, leg in enumerate(legs):
                if leg["type"] == "INTERCHANGE_TO":
                    interchange_points.append(f"{stations[i]['station_id']} -> {stations[i+1]['station_id']}")
                    
            return {
                "found": True,
                "stations": stations,
                "interchange_points": interchange_points,
                "total_time_min": record["total_time_min"]
            }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    # 核心修正：加入 WHERE start <> other 預防搜尋到自身站體導致最短路徑算法崩潰
    query = """
    MATCH (start:Station {station_id: $delayed_station_id})
    MATCH (other:Station)
    WHERE start <> other
    MATCH p = shortestPath((start)-[:CONNECTS_TO|INTERCHANGE_TO*1..10]->(other))
    WHERE length(p) > 0 AND length(p) <= $hops
    RETURN DISTINCT other.station_id AS station_id, other.name AS name, length(p) AS hops_away, other.lines AS lines_affected
    ORDER BY hops_away ASC
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(query, delayed_station_id=delayed_station_id, hops=hops)
            return [dict(record) for record in result]


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    query = """
    MATCH (start:Station {station_id: $station_id})-[r:CONNECTS_TO|INTERCHANGE_TO]->(other:Station)
    RETURN other.station_id AS station_id, 
           other.name AS name, 
           type(r) AS rel_type, 
           r.line AS line, 
           r.service_type AS service_type, 
           r.travel_time_min AS travel_time_min
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(query, station_id=station_id)
            return [dict(record) for record in result]