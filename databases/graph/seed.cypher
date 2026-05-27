
// 1. 建立唯一性約束，確保車站 ID 不會重複，並加速節點查詢
CREATE CONSTRAINT station_id_unique IF NOT EXISTS
FOR (s:Station) REQUIRE s.station_id IS UNIQUE;

// 2. 建立常用查詢索引，加速多元演算法過濾
CREATE INDEX station_network_idx IF NOT EXISTS
FOR (s:Station) ON (s.network);

CREATE INDEX station_name_idx IF NOT EXISTS
FOR (s:Station) ON (s.name);


// ============================================================================
// 概念建模範例 (供團隊開發 seed_neo4j.py 參考)
// ============================================================================

/*
// 建立捷運車站範例
CREATE (:Station {
  station_id: "MS01", 
  name: "Central Square", 
  network: "metro", 
  lines: ["M1", "M2"]
});

// 建立國鐵車站範例
CREATE (:Station {
  station_id: "NR01", 
  name: "Central Station", 
  network: "rail", 
  lines: ["NR1", "NR2"]
});

// 建立路線連線範例 (包含時間與票價權重)
MATCH (a:Station {station_id: "MS20"}), (b:Station {station_id: "MS05"})
CREATE (a)-[:CONNECTS_TO {
  line: "M1", 
  service_type: "normal", 
  travel_time_min: 2, 
  fare_standard: 0.30
}]->(b);

// 建立快車線路連線範例 (跳過普通車站，直達時間更短)
MATCH (a:Station {station_id: "NR01"}), (b:Station {station_id: "NR03"})
CREATE (a)-[:CONNECTS_TO {
  line: "NR1", 
  service_type: "express", 
  travel_time_min: 25, 
  fare_standard: 1.80, 
  fare_first: 3.00
}]->(b);

// 建立雙向跨網路轉乘通道範例
MATCH (m:Station {station_id: "MS01"}), (r:Station {station_id: "NR01"})
CREATE (m)-[:INTERCHANGE_TO {travel_time_min: 0, fare: 0.0}]->(r),
       (r)-[:INTERCHANGE_TO {travel_time_min: 0, fare: 0.0}]->(m);
*/// Deprecated: seeding is now done via skeleton/seed_neo4j.py
// which loads data directly from train-mock-data/ JSON files.
//
// If you prefer Cypher-file seeding, implement your graph schema here.
// Run with: python skeleton/seed_neo4j.py (or via the Neo4j Browser)
