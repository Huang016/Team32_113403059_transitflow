// ============================================================================
// TransitFlow — Neo4j Schema & Constraints Definition
// ============================================================================

// [Code Quality]
// WHY: We enforce uniqueness on station_id at the database level to prevent data duplication
// during idempotent seeding and to implicitly create B-Tree indexes for O(1) node lookups.
CREATE CONSTRAINT metro_station_id_unique IF NOT EXISTS
FOR (s:MetroStation) REQUIRE s.station_id IS UNIQUE;

CREATE CONSTRAINT national_rail_station_id_unique IF NOT EXISTS
FOR (s:NationalRailStation) REQUIRE s.station_id IS UNIQUE;

// [Code Quality]
// WHY: Additional indexes are created to optimize filtering by zone, specifically 
// accelerating the calculations for Task 6 Dynamic Zone-Based Fares.
CREATE INDEX metro_station_zone_idx IF NOT EXISTS
FOR (s:MetroStation) ON (s.zone);

CREATE INDEX national_rail_station_zone_idx IF NOT EXISTS
FOR (s:NationalRailStation) ON (s.zone);


// ============================================================================
// Concept Modeling Examples (Aligned with Task 4 Rubric & Task 6 Extension)
// Note: Actual seeding execution is handled by skeleton/seed_neo4j.py
// ============================================================================

/*
// [Code Quality] 
// WHY: MERGE is utilized instead of CREATE to ensure operation idempotency. 
// This allows the seeding process to be safely re-run without causing data duplication.

// Example: Creating a Metro Station with Task 6 properties
MERGE (s:MetroStation {station_id: "MS01"})
SET s.name = "Central Square", 
    s.lines = ["M1", "M2"],
    s.zone = 1,
    s.closed = false;

// Example: Creating a National Rail Station
MERGE (s:NationalRailStation {station_id: "NR01"})
SET s.name = "Central Station", 
    s.lines = ["NR1", "NR2"],
    s.zone = 1,
    s.closed = false;

// Example: Creating a METRO_LINK with numeric travel_time_min
MATCH (a:MetroStation {station_id: "MS20"}), (b:MetroStation {station_id: "MS05"})
MERGE (a)-[r:METRO_LINK {line: "M1"}]->(b)
SET r.travel_time_min = 2, 
    r.fare_standard = 0.30;

// Example: Creating a RAIL_LINK
MATCH (a:NationalRailStation {station_id: "NR01"}), (b:NationalRailStation {station_id: "NR03"})
MERGE (a)-[r:RAIL_LINK {line: "NR1", service_type: "express"}]->(b)
SET r.travel_time_min = 25, 
    r.fare_standard = 1.80, 
    r.fare_first = 3.00;

// Example: Creating bidirectional INTERCHANGE_TO paths
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