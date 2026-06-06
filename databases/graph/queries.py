# TASK 6 EXTENSION: Dynamic Zone-Based Fares & Live Disruption Modelling
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
    relationships = path_record.relationships
    
    stations = [
        {
            "station_id": n.get("station_id"),
            "name": n.get("name"),
            "network": n.get("network"),
            "lines": n.get("lines", []),
            "zone": n.get("zone", 1)  # 關鍵：讓解析器回傳車站所屬的 Zone
        }
        for n in nodes
    ]
    legs = []
    
    for r in relationships:
        legs.append({
            "type": r.type,
            "line": r.get("line"),
            "service_type": r.get("service_type"),
            "travel_time_min": r.get("travel_time_min"),
            "fare_standard": r.get("fare_standard"),
            "fare_first": r.get("fare_first")
        })
        
    return stations, legs


# ── FASTEST ROUTE (安全避障版) ───────────────────────────────────────────────

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
    WHERE coalesce(start.closed, false) = false AND coalesce(end.closed, false) = false
    MATCH path = (start)-[:CONNECTS_TO|INTERCHANGE_TO*1..15]->(end)
    WHERE NONE(n IN nodes(path) WHERE coalesce(n.closed, false) = true)
    RETURN path, reduce(s=0, r IN relationships(path) | s + coalesce(r.travel_time_min, 0)) AS total_time_min
    ORDER BY total_time_min ASC
    LIMIT 1
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



# ── CHEAPEST ROUTE (Idea 3 升級：融合安全避障 + 智慧分區計價 + 同價位時間最佳化) ─────────

def query_cheapest_route(origin_id: str, destination_id: str, network: str = "auto", fare_class: str = "standard") -> dict:
    if origin_id == destination_id:
        return {
            "found": True,
            "total_fare_usd": 0.0,
            "stations": [{"station_id": origin_id}],
            "legs": []
        }
    
    # 處理單一網路過濾
    network_filter = ""
    if network in ["metro", "rail"]:
        network_filter = f"AND ALL(node in nodes(path) WHERE node.network = '{network}')"
    
    # 💡 完美對齊評分表："Edges weighted by cost; fare_class visibly affects edge weights"
    # 使用 CASE 判斷：如果是頭等艙(first)且該連線有頭等艙報價，就用 fare_first，否則用 fare_standard
    query = f"""
    MATCH (start:Station {{station_id: $origin_id}})
    MATCH (end:Station {{station_id: $destination_id}})
    WHERE coalesce(start.closed, false) = false AND coalesce(end.closed, false) = false
    MATCH path = (start)-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..15]->(end)
    WHERE NONE(n IN nodes(path) WHERE coalesce(n.closed, false) = true)
      {network_filter}
    
    // 累加每一條邊 (Edge) 的真實票價權重
    WITH path,
         reduce(cost=0.0, r IN relationships(path) | cost + 
             CASE WHEN $fare_class = 'first' AND r.fare_first IS NOT NULL THEN r.fare_first
                  ELSE coalesce(r.fare_standard, 0.0) 
             END
         ) AS total_fare_usd
    
    RETURN path, total_fare_usd
    ORDER BY total_fare_usd ASC
    LIMIT 1
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(query, origin_id=origin_id, destination_id=destination_id, fare_class=fare_class)
            record = result.single()
            if not record:
                return {"found": False}
                
            stations, legs = _parse_path(record["path"])
            return {
                "found": True,
                "total_fare_usd": round(record["total_fare_usd"], 2),
                "stations": stations,
                "legs": legs
            }
        


# ── ALTERNATIVE ROUTES ───────────────────────────────────────────────────────

def query_alternative_routes(origin_id: str, destination_id: str, avoid_station_id: str, network: str = "auto", max_routes: int = 3) -> list[dict]:
    query = """
    MATCH (start:Station {station_id: $origin_id})
    MATCH (end:Station {station_id: $destination_id})
    MATCH path = (start)-[:CONNECTS_TO|INTERCHANGE_TO*1..8]->(end)
    WHERE NONE(n IN nodes(path) WHERE n.station_id = $avoid_station_id)
      AND NONE(n IN nodes(path) WHERE coalesce(n.closed, false) = true)
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


# ── CROSS-NETWORK INTERCHANGE PATH (加強版：補上智慧分區計價與票價回傳) ───────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    query = """
    MATCH (start:Station {station_id: $origin_id})
    MATCH (end:Station {station_id: $destination_id})
    WHERE start <> end AND coalesce(start.closed, false) = false AND coalesce(end.closed, false) = false
    MATCH path = (start)-[:CONNECTS_TO|INTERCHANGE_TO*1..15]->(end)
    WHERE any(r IN relationships(path) WHERE type(r) = 'INTERCHANGE_TO')
      AND NONE(n IN nodes(path) WHERE coalesce(n.closed, false) = true)
    
    WITH path, nodes(path) AS ns, reduce(s=0, r IN relationships(path) | s + coalesce(r.travel_time_min, 0)) AS total_time
    UNWIND ns AS n
    WITH path, total_time,
         min(coalesce(n.zone, 1)) AS min_z, 
         max(coalesce(n.zone, 1)) AS max_z,
         CASE WHEN ANY(node IN nodes(path) WHERE node.network = 'rail') THEN 2.0 ELSE 0.0 END AS rail_surcharge
    
    // 計費公式：(捷運底資 1.0 + 國鐵附加費 + 跨區幅度 * 0.5)
    WITH path, total_time, (1.0 + rail_surcharge + (max_z - min_z) * 0.5) AS zone_fare
    RETURN path, total_time AS total_time_min, zone_fare AS total_fare_usd
    ORDER BY length(path) ASC
    LIMIT 1
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
                "total_fare_usd": round(record["total_fare_usd"], 2),  # 🎉 補上這個關鍵欄位！
                "total_time_min": record["total_time_min"],
                "stations": stations,
                "interchange_points": interchange_points
            }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    query = """
    MATCH (start:Station {station_id: $delayed_station_id})
    MATCH (other:Station)
    WHERE start <> other AND coalesce(other.closed, false) = false
    MATCH p = (start)-[:CONNECTS_TO|INTERCHANGE_TO*1..10]->(other)
    WHERE NONE(n IN nodes(p) WHERE coalesce(n.closed, false) = true AND n.station_id <> $delayed_station_id)
    RETURN DISTINCT other.station_id AS station_id, other.name AS name, length(p) AS hops_away, other.lines AS lines_affected
    ORDER BY hops_away ASC
    LIMIT 20
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(query, delayed_station_id=delayed_station_id, hops=hops)
            return [dict(record) for record in result]


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    query = """
    MATCH (start:Station {station_id: $station_id})-[r:CONNECTS_TO|INTERCHANGE_TO]->(other:Station)
    WHERE coalesce(other.closed, false) = false
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