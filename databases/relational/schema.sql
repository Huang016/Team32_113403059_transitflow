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
-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
-- ============================================================

-- =========================================================================
-- 1. 獨立主表 (無任何外鍵，必須最先建立)
-- =========================================================================
-- 使用者帳號表 (手冊命名為 users)
CREATE TABLE registered_users (
    user_id VARCHAR(10) PRIMARY KEY,
    full_name VARCHAR(200) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(60) NOT NULL, -- 預留 60 碼給 bcrypt
    phone VARCHAR(20),
    date_of_birth DATE NOT NULL,
    secret_question VARCHAR(255),
    secret_answer_hash VARCHAR(255),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);


-- 國家鐵路車站表
CREATE TABLE national_rail_stations (
    station_id VARCHAR(10) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    lines JSONB NOT NULL, -- 規範：使用 JSONB 陣列
    is_interchange_national_rail BOOLEAN NOT NULL DEFAULT FALSE,
    interchange_national_rail_lines JSONB,
    is_interchange_metro BOOLEAN NOT NULL DEFAULT FALSE,
    interchange_metro_station_id VARCHAR(10) -- 先不設硬 FK 避免 Circular Reference
);

-- 城市地鐵車站表
CREATE TABLE metro_stations (
    station_id VARCHAR(10) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    lines JSONB NOT NULL,
    is_interchange_metro BOOLEAN NOT NULL DEFAULT FALSE,
    interchange_metro_lines JSONB,
    is_interchange_national_rail BOOLEAN NOT NULL DEFAULT FALSE,
    interchange_national_rail_station_id VARCHAR(10) REFERENCES national_rail_stations(station_id) ON DELETE SET NULL
);

-- 補上國家鐵路指向地鐵的轉乘 FK
ALTER TABLE national_rail_stations
ADD CONSTRAINT fk_nr_interchange_metro
FOREIGN KEY (interchange_metro_station_id)
REFERENCES metro_stations(station_id) ON DELETE SET NULL;

-- =========================================================================
-- 2. 第二層表 (依賴上述車站主表)
-- =========================================================================

-- 國家鐵路時刻表
CREATE TABLE national_rail_schedules (
    schedule_id VARCHAR(20) PRIMARY KEY,
    line VARCHAR(10) NOT NULL,
    service_type VARCHAR(20) NOT NULL CHECK (service_type IN ('normal', 'express')),
    direction VARCHAR(20) NOT NULL,
    origin_station_id VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    stops_in_order JSONB NOT NULL, -- 規範：必須使用 JSONB
    passed_through_stations JSONB,
    first_train_time TIME NOT NULL,
    last_train_time TIME NOT NULL,
    travel_time_from_origin_min JSONB NOT NULL,
    fare_classes JSONB NOT NULL,
    frequency_min INT,
    operates_on JSONB NOT NULL
);

-- 城市地鐵時刻表
CREATE TABLE metro_schedules (
    schedule_id VARCHAR(20) PRIMARY KEY,
    line VARCHAR(10) NOT NULL,
    direction VARCHAR(20) NOT NULL,
    origin_station_id VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    stops_in_order JSONB NOT NULL,
    first_train_time TIME NOT NULL,
    last_train_time TIME NOT NULL,
    travel_time_from_origin_min JSONB NOT NULL,
    base_fare_usd NUMERIC(10, 2) NOT NULL,
    per_stop_rate_usd NUMERIC(10, 2) NOT NULL,
    frequency_min INT,
    operates_on JSONB NOT NULL
);

-- 國家鐵路座位表 (核心：必須攤平，不可使用嵌套 JSON)
CREATE TABLE national_rail_seats (
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    seat_id VARCHAR(10) NOT NULL,
    coach VARCHAR(5) NOT NULL,
    fare_class VARCHAR(20) NOT NULL CHECK (fare_class IN ('first', 'standard')),
    seat_row INT NOT NULL,
    seat_column VARCHAR(5) NOT NULL,
    PRIMARY KEY (schedule_id, seat_id) -- 複合主鍵
);

-- =========================================================================
-- 3. 交易紀錄表 (核心橋樑表)
-- =========================================================================

-- 國家鐵路訂票紀錄表 (手冊命名為 national_rail_bookings)
CREATE TABLE national_rail_bookings (
    booking_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(10) NOT NULL REFERENCES registered_users(user_id) ON DELETE RESTRICT,
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    travel_date DATE NOT NULL,
    departure_time TIME NOT NULL,
    ticket_type VARCHAR(20) NOT NULL,
    fare_class VARCHAR(20) NOT NULL,
    coach VARCHAR(5) NOT NULL,
    seat_id VARCHAR(10) NOT NULL,
    stops_travelled INT NOT NULL,
    amount_usd NUMERIC(10, 2) NOT NULL CHECK (amount_usd >= 0),
    status VARCHAR(20) NOT NULL CHECK (status IN ('completed', 'confirmed', 'cancelled')),
    booked_at TIMESTAMPTZ NOT NULL,
    travelled_at TIMESTAMPTZ
);
agsdg
-- 城市地鐵搭乘歷史紀錄表 (手冊命名為 metro_trips)
CREATE TABLE metro_trips (
    trip_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(10) NOT NULL REFERENCES registered_users(user_id) ON DELETE RESTRICT,
    schedule_id VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    travel_date DATE NOT NULL,
    ticket_type VARCHAR(20) NOT NULL,
    day_pass_ref VARCHAR(20) REFERENCES metro_trips(trip_id) ON DELETE SET NULL, -- 自遞迴外鍵
    stops_travelled INT,
    amount_usd NUMERIC(10, 2) NOT NULL CHECK (amount_usd >= 0),
    status VARCHAR(20) NOT NULL,
    purchased_at TIMESTAMPTZ,
    travelled_at TIMESTAMPTZ NOT NULL
);

-- =========================================================================
-- 4. 付款與回饋表 (嚴格實體外鍵分離版)
-- =========================================================================

CREATE TABLE national_rail_payments (
    payment_id VARCHAR(15) PRIMARY KEY,
    booking_id VARCHAR(20) NOT NULL REFERENCES national_rail_bookings(booking_id) ON DELETE RESTRICT,
    amount_usd NUMERIC(10, 2) NOT NULL CHECK (amount_usd >= 0),
    method VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    paid_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE metro_payments (
    payment_id VARCHAR(15) PRIMARY KEY,
    trip_id VARCHAR(20) NOT NULL REFERENCES metro_trips(trip_id) ON DELETE RESTRICT,
    amount_usd NUMERIC(10, 2) NOT NULL CHECK (amount_usd >= 0),
    method VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    paid_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE national_rail_feedback (
    feedback_id VARCHAR(15) PRIMARY KEY,
    booking_id VARCHAR(20) NOT NULL REFERENCES national_rail_bookings(booking_id) ON DELETE RESTRICT,
    user_id VARCHAR(10) NOT NULL REFERENCES registered_users(user_id) ON DELETE RESTRICT,
    rating INT CHECK (rating >= 1 AND rating <= 5),
    comment TEXT,
    submitted_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE metro_feedback (
    feedback_id VARCHAR(15) PRIMARY KEY,
    trip_id VARCHAR(20) NOT NULL REFERENCES metro_trips(trip_id) ON DELETE RESTRICT,
    user_id VARCHAR(10) NOT NULL REFERENCES registered_users(user_id) ON DELETE RESTRICT,
    rating INT CHECK (rating >= 1 AND rating <= 5),
    comment TEXT,
    submitted_at TIMESTAMPTZ NOT NULL
);

-- =========================================================================
-- 5. 外鍵效能索引 (手冊要求：手動為外鍵建立索引)
-- =========================================================================
CREATE INDEX idx_nrb_user_id ON national_rail_bookings(user_id);
CREATE INDEX idx_mt_user_id ON metro_trips(user_id);
CREATE INDEX idx_nrb_schedule_id ON national_rail_bookings(schedule_id);
CREATE INDEX idx_mt_schedule_id ON metro_trips(schedule_id);
CREATE INDEX idx_nrp_booking_id ON national_rail_payments(booking_id);
CREATE INDEX idx_mp_trip_id ON metro_payments(trip_id);
CREATE INDEX idx_nrf_booking_id ON national_rail_feedback(booking_id);
CREATE INDEX idx_mf_trip_id ON metro_feedback(trip_id);

-- ============================================================
--  VECTOR SCHEMA  (RAG / Help Desk) — do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,  -- 'refund', 'booking', 'conduct'
    content     TEXT         NOT NULL,
    embedding   vector(768),
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS policy_documents_embedding_hnsw_idx 
ON policy_documents USING hnsw (embedding vector_cosine_ops);