// ============================================================================
// TransitFlow — Neo4j Schema & Constraints Definition
// ============================================================================

// 1. 建立唯一性約束，確保車站 ID 不會重複 (對齊 Task 4 雙標籤設計)
CREATE CONSTRAINT metro_station_id_unique IF NOT EXISTS
FOR (s:MetroStation) REQUIRE s.station_id IS UNIQUE;

CREATE CONSTRAINT national_rail_station_id_unique IF NOT EXISTS
FOR (s:NationalRailStation) REQUIRE s.station_id IS UNIQUE;

// 2. 建立常用查詢索引，加速多元演算法過濾與 Task 6 的分區計算
CREATE INDEX metro_station_zone_idx IF NOT EXISTS
FOR (s:MetroStation) ON (s.zone);

CREATE INDEX national_rail_station_zone_idx IF NOT EXISTS
FOR (s:NationalRailStation) ON (s.zone);


// ============================================================================
// 概念建模範例 (已全面對齊 Task 4 Rubric 與 Task 6 Extension 標準)
// 註：實際匯入動作由 skeleton/seed_neo4j.py 執行
// ============================================================================

/*
//  建立捷運車站範例 (使用 MERGE 確保冪等性，並包含 Task 6 擴充屬性)
MERGE (s:MetroStation {station_id: "MS01"})
SET s.name = "Central Square", 
    s.lines = ["M1", "M2"],
    s.zone = 1,
    s.closed = false;

//  建立國鐵車站範例
MERGE (s:NationalRailStation {station_id: "NR01"})
SET s.name = "Central Station", 
    s.lines = ["NR1", "NR2"],
    s.zone = 1,
    s.closed = false;

//  建立捷運路線連線範例 (連線改為 METRO_LINK，確保數值型態的 travel_time_min)
MATCH (a:MetroStation {station_id: "MS20"}), (b:MetroStation {station_id: "MS05"})
MERGE (a)-[r:METRO_LINK {line: "M1"}]->(b)
SET r.travel_time_min = 2, 
    r.fare_standard = 0.30;

//  建立國鐵路線連線範例 (連線改為 RAIL_LINK)
MATCH (a:NationalRailStation {station_id: "NR01"}), (b:NationalRailStation {station_id: "NR03"})
MERGE (a)-[r:RAIL_LINK {line: "NR1", service_type: "express"}]->(b)
SET r.travel_time_min = 25, 
    r.fare_standard = 1.80, 
    r.fare_first = 3.00;

//  建立雙向跨網路轉乘通道範例 (INTERCHANGE_TO，確保雙向 MERGE)
MATCH (m:MetroStation {station_id: "MS01"}), (r:NationalRailStation {station_id: "NR01"})
MERGE (m)-[:INTERCHANGE_TO {
  travel_time_min: 0,
  fare_standard: 0.0,
  fare_first: 0.0
}]->(r)
MERGE (r)-[:INTERCHANGE_TO {
  travel_time_min: 0,
  fare_standard: 0.0,
  fare_first: 0.0
}]->(m);
*/