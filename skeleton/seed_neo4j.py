# TASK 6 EXTENSION: Dynamic Zone-Based Fares & Live Disruption Modelling
"""
TransitFlow — Neo4j Seeder (基於真實地圖地理分區 100% 完美版)
Run once after starting Docker:
    python skeleton/seed_neo4j.py
"""

import json
import os
import sys

sys.path.insert(0, ".")

from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "train-mock-data")
)

# 🗺️ 依據真實地圖「核心環狀線與幾何形狀」嚴格定義的精準分區對照表 (總共 30 站)
STATION_ZONES = {
    # Zone 1: 核心大環線與其內部 (The Inner Ring)
    "MS01": 1, "MS02": 1, "MS03": 1, "MS04": 1, "MS07": 1, "MS08": 1, "MS12": 1, "MS18": 1,
    "NR01": 1, "NR02": 1, "NR03": 1,
    
    # Zone 2: 環線向外輻射的近郊站 (First outward branches)
    "MS05": 2, "MS06": 2, "MS09": 2, "MS10": 2, "MS14": 2, "MS17": 2,
    "NR04": 2, "NR06": 2,
    
    # Zone 3: 地圖邊界的遠郊末端站群 (Outer extremities)
    "MS11": 3, "MS13": 3, "MS15": 3, "MS16": 3, "MS19": 3, "MS20": 3,
    "NR05": 3, "NR07": 3, "NR08": 3, "NR09": 3, "NR10": 3
}


def _load(filename):
    with open(os.path.join(_DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def seed():
    print("Loading JSON mock data...")
    metro_stations = _load("metro_stations.json")
    rail_stations = _load("national_rail_stations.json")
    rail_schedules = _load("national_rail_schedules.json")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:
        # 1. 清空舊資料
        session.run("MATCH (n) DETACH DELETE n")
        print("  Cleared existing graph data")

        # 2. 建立約束與索引
        session.run("CREATE CONSTRAINT station_id_unique IF NOT EXISTS FOR (s:Station) REQUIRE s.station_id IS UNIQUE")
        session.run("CREATE INDEX station_network_idx IF NOT EXISTS FOR (s:Station) ON (s.network)")
        session.run("CREATE INDEX station_name_idx IF NOT EXISTS FOR (s:Station) ON (s.name)")
        print("  Created constraints and indexes")

        # 3. 建立捷運車站 (Nodes)
        for station in metro_stations:
            sid = station["station_id"]
            zone = STATION_ZONES.get(sid, 1)

            session.run("""
                CREATE (:Station {
                    station_id: $id, 
                    name: $name, 
                    network: 'metro', 
                    lines: $lines,
                    closed: false,
                    zone: $zone
                })
            """, id=sid, name=station["name"], lines=station["lines"], zone=zone)

        # 4. 建立國鐵車站 (Nodes)
        for station in rail_stations:
            sid = station["station_id"]
            zone = STATION_ZONES.get(sid, 1)

            session.run("""
                CREATE (:Station {
                    station_id: $id, 
                    name: $name, 
                    network: 'rail', 
                    lines: $lines,
                    closed: false,
                    zone: $zone
                })
            """, id=sid, name=station["name"], lines=station["lines"], zone=zone)
        print("  Created all Station nodes with mathematically precise geographic Zones!")

        # 5. 建立捷運連線 (Edges)
        for station in metro_stations:
            for adj in station["adjacent_stations"]:
                session.run("""
                    MATCH (a:Station {station_id: $id}), (b:Station {station_id: $adj_id})
                    MERGE (a)-[r:CONNECTS_TO {line: $line, service_type: 'normal'}]->(b)
                    SET r.travel_time_min = $time, 
                        r.fare_standard = 0.30
                """, id=station["station_id"], adj_id=adj["station_id"], 
                     line=adj["line"], time=adj["travel_time_min"])

        # 6. 建立國鐵普通車連線 (Edges)
        for station in rail_stations:
            for adj in station["adjacent_stations"]:
                session.run("""
                    MATCH (a:Station {station_id: $id}), (b:Station {station_id: $adj_id})
                    MERGE (a)-[r:CONNECTS_TO {line: $line, service_type: 'normal'}]->(b)
                    SET r.travel_time_min = $time, 
                        r.fare_standard = 1.50, 
                        r.fare_first = 2.50
                """, id=station["station_id"], adj_id=adj["station_id"], 
                     line=adj["line"], time=adj["travel_time_min"])

        # 7. 建立國鐵快車連線 (Edges)
        for sch in rail_schedules:
            if sch["service_type"] == "express":
                stops = sch["stops_in_order"]
                times = sch["travel_time_from_origin_min"]
                fare_std = sch["fare_classes"]["standard"]["per_stop_rate_usd"]
                fare_1st = sch["fare_classes"]["first"]["per_stop_rate_usd"]
                
                for i in range(len(stops) - 1):
                    origin_id = stops[i]
                    dest_id = stops[i+1]
                    travel_time = times[dest_id] - times[origin_id]
                    
                    session.run("""
                        MATCH (a:Station {station_id: $o_id}), (b:Station {station_id: $d_id})
                        MERGE (a)-[r:CONNECTS_TO {line: $line, service_type: 'express'}]->(b)
                        SET r.travel_time_min = $time, 
                            r.fare_standard = $f_std, 
                            r.fare_first = $f_1st
                    """, o_id=origin_id, d_id=dest_id, line=sch["line"], 
                         time=travel_time, f_std=fare_std, f_1st=fare_1st)

        # 8. 建立跨網絡轉乘關連 (Edges)
        for station in metro_stations:
            if station.get("is_interchange_national_rail"):
                nr_id = station["interchange_national_rail_station_id"]
                ms_id = station["station_id"]
                session.run("""
                    MATCH (m:Station {station_id: $ms_id}), (r:Station {station_id: $nr_id})
                    MERGE (m)-[:INTERCHANGE_TO {
                        travel_time_min: 0,
                        fare_standard: 0.0,
                        fare_first: 0.0
                    }]->(r)
                    MERGE (r)-[:INTERCHANGE_TO {
                        travel_time_min: 0,
                        fare_standard: 0.0,
                        fare_first: 0.0
                    }]->(m)
                """, ms_id=ms_id, nr_id=nr_id)
                
        print("  Created all CONNECTS_TO and INTERCHANGE_TO relationships")

    driver.close()
    print("\n✅ Neo4j graph seeded successfully with Real Map Zones.")


if __name__ == "__main__":
    print("Connecting to Neo4j...")
    seed()