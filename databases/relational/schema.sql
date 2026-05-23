-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--     1. Relational  → dual-network transit data you design below
--     2. Vector      → policy documents for RAG (provided — do not modify)
-- ============================================================

-- ============================================================
--  STUDENT TASK — Design and create your relational tables here
-- ============================================================

-- =========================================================================
-- 1. 獨立主表 (無任何外鍵，必須最先建立)
-- =========================================================================

-- 使用者帳號表
CREATE TABLE registered_users (
    user_id VARCHAR(10) PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    phone VARCHAR(20),
    date_of_birth DATE,
    secret_question TEXT,
    secret_answer_hash TEXT,
    registered_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- 國家鐵路車站表
CREATE TABLE national_rail_stations (
    station_id VARCHAR(10) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    lines VARCHAR(10)[],
    is_interchange_national_rail BOOLEAN DEFAULT FALSE,
    interchange_national_rail_lines VARCHAR(10)[],
    is_interchange_metro BOOLEAN DEFAULT FALSE,
    interchange_metro_station_id VARCHAR(10)
);

-- 城市地鐵車站表
CREATE TABLE metro_stations (
    station_id VARCHAR(10) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    lines VARCHAR(10)[],
    is_interchange_metro BOOLEAN DEFAULT FALSE,
    interchange_metro_lines VARCHAR(10)[],
    is_interchange_national_rail BOOLEAN DEFAULT FALSE,
    interchange_national_rail_station_id VARCHAR(10)
);

ALTER TABLE national_rail_stations
ADD CONSTRAINT fk_nr_interchange_metro
FOREIGN KEY (interchange_metro_station_id)
REFERENCES metro_stations(station_id);

ALTER TABLE metro_stations
ADD CONSTRAINT fk_metro_interchange_rail
FOREIGN KEY (interchange_national_rail_station_id)
REFERENCES national_rail_stations(station_id); 
--轉乘的FK

-- =========================================================================
-- 2. 第二層表 (依賴上述車站主表)
-- =========================================================================

-- 國家鐵路時刻表
CREATE TABLE national_rail_schedules (
    schedule_id VARCHAR(20) PRIMARY KEY,
    line VARCHAR(10) NOT NULL,
    service_type VARCHAR(20) NOT NULL,
    direction VARCHAR(20) NOT NULL,
    origin_station_id VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id),
    stops_in_order VARCHAR(10)[],
    passed_through_stations VARCHAR(10)[],
    first_train_time TIME,
    last_train_time TIME,
    travel_time_from_origin_min JSONB,
    fare_classes JSONB,
    frequency_min INT,
    operates_on VARCHAR(5)[]
);


-- 城市地鐵時刻表
CREATE TABLE metro_schedules (
    schedule_id VARCHAR(20) PRIMARY KEY,
    line VARCHAR(10) NOT NULL,
    direction VARCHAR(20) NOT NULL,
    origin_station_id VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id),
    stops_in_order VARCHAR(10)[],
    first_train_time TIME,
    last_train_time TIME,
    travel_time_from_origin_min JSONB,
    base_fare_usd NUMERIC(5, 2),
    per_stop_rate_usd NUMERIC(5, 2),
    frequency_min INT,
    operates_on VARCHAR(5)[]
);

-- 國家鐵路座位模板表
CREATE TABLE national_rail_seat_layouts (
    layout_id VARCHAR(10) PRIMARY KEY,
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id),
    coaches JSONB NOT NULL
);

-- =========================================================================
-- 3. 交易交易紀錄表 (核心橋樑表，依賴使用者、班次與車站)
-- =========================================================================

-- 國家鐵路訂票紀錄表
CREATE TABLE bookings (
    booking_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(10) NOT NULL REFERENCES registered_users(user_id),
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id),
    origin_station_id VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id),
    travel_date DATE NOT NULL,
    departure_time TIME,
    ticket_type VARCHAR(20),
    fare_class VARCHAR(20),
    coach VARCHAR(5),
    seat_id VARCHAR(10),
    stops_travelled INT,
    amount_usd NUMERIC(10, 2) NOT NULL CHECK (amount_usd >= 0),
    status VARCHAR(20) NOT NULL,
    booked_at TIMESTAMPTZ,
    travelled_at TIMESTAMPTZ
);

-- 城市地鐵搭乘歷史紀錄表
CREATE TABLE metro_travel_history (
    trip_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(10) NOT NULL REFERENCES registered_users(user_id),
    schedule_id VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id),
    origin_station_id VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id),
    travel_date DATE NOT NULL,
    ticket_type VARCHAR(20),
    day_pass_ref VARCHAR(20),
    stops_travelled INT,
    amount_usd NUMERIC(10, 2) NOT NULL CHECK (amount_usd >= 0),
    status VARCHAR(20) NOT NULL,
    purchased_at TIMESTAMPTZ,
    travelled_at TIMESTAMPTZ
);
-- =========================================================================
-- 4. 付款與回饋表 (最後建立，因其依賴上述所有的交易單據)
-- =========================================================================

-- 國鐵和城市地鐵付款紀錄表
CREATE TABLE rail_payments (
    payment_id VARCHAR(10) PRIMARY KEY,
    booking_id VARCHAR(20) NOT NULL REFERENCES bookings(booking_id),
    amount_usd NUMERIC(10, 2) NOT NULL CHECK (amount_usd >= 0),
    method VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    paid_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE metro_payments (
    payment_id VARCHAR(10) PRIMARY KEY,
    trip_id VARCHAR(20) NOT NULL REFERENCES metro_travel_history(trip_id),
    amount_usd NUMERIC(10, 2) NOT NULL CHECK (amount_usd >= 0),
    method VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    paid_at TIMESTAMPTZ NOT NULL
);
-- 國鐵和城市地鐵乘客回饋評價表
CREATE TABLE rail_feedback (
    feedback_id VARCHAR(10) PRIMARY KEY,
    booking_id VARCHAR(20) NOT NULL REFERENCES bookings(booking_id),
    user_id VARCHAR(10) NOT NULL REFERENCES registered_users(user_id),
    rating INT CHECK (rating >= 1 AND rating <= 5),
    comment TEXT,
    submitted_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE metro_feedback (
    feedback_id VARCHAR(10) PRIMARY KEY,
    trip_id VARCHAR(20) NOT NULL REFERENCES metro_travel_history(trip_id),
    user_id VARCHAR(10) NOT NULL REFERENCES registered_users(user_id),
    rating INT CHECK (rating >= 1 AND rating <= 5),
    comment TEXT,
    submitted_at TIMESTAMPTZ NOT NULL
);



-- ============================================================
--  VECTOR SCHEMA  (RAG / Help Desk) — do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,  -- 'refund', 'booking', 'conduct'
    content     TEXT         NOT NULL,
    -- 768-dim  → Ollama nomic-embed-text (default)
    -- 3072-dim → Gemini gemini-embedding-001
    -- If you switch LLM_PROVIDER to gemini, change to vector(3072) and reset the database.
    embedding   vector(768),
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

-- Index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS policy_documents_embedding_hnsw_idx
ON policy_documents USING hnsw (embedding vector_cosine_ops);
