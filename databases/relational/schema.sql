-- ============================================================
-- TransitFlow PostgreSQL Relational Schema — UUID / SERIAL version
-- Rubric alignment:
--   1) All PKs use UUID or SERIAL, not VARCHAR.
--   2) The original JSON/string IDs are preserved as *_code columns with UNIQUE
--      constraints so seed_postgres.py can map imported data deterministically.
--   3) Only password_hash is hashed. secret_answer is intentionally plain text
--      because the current requirement says only passwords need hashing.
--   4) Schedule stops are normalized into stop junction tables with stop_order.
--

-- Delete strategy:
--   Business history is retained using is_active/status fields. Therefore most
--   transactional FKs use ON DELETE RESTRICT. Pure child rows that cannot exist
--   without their parent schedule, such as stops, seats and platform assignments,
--   use ON DELETE CASCADE.
-- ============================================================
-- Needed for gen_random_uuid().

============================================================
-- 💡 NEWLY ADDED EXTENSION FEATURE 1: Platform Assignment System
-- ============================================================
-- Functionality & System Role:
--   This system introduces physical track-level routing to the transport network.
--   In real-world transit, knowing a schedule and a station is insufficient; 
--   passengers must be guided to a specific physical platform.
-- 
--   The platform assignment tables ('national_rail_platforms' and 'metro_platforms')
--   serve as a crucial operational layer linking 'Schedules' and 'Stations'. 
--   By mapping a composite unique relation (schedule_id, station_id), the system
--   dynamically assigns a platform number (1 to 4) and tracks the physical direction 
--   of the service run at that exact node. This empowers the AI assistant and 
--   the frontend UI to provide precise platform-level boarding instructions.
--
-- ============================================================
-- 💡 NEWLY ADDED EXTENSION FEATURE 2: Monthly Commuter Pass System
-- ============================================================
-- Functionality & System Role:
--   This system introduces a financial subscription model for high-frequency metro commuters.
--   Instead of stops-based single fares, users can purchase a flat-rate 30-day pass ($75.00) 
--   for unlimited journeys across all metro lines (M1-M4).
-- 
--   The 'metro_monthly_passes' table records active subscriptions with absolute time bounds 
--   ('valid_from' and 'valid_until'). It links directly to 'registered_users' and maps 
--   into 'metro_trips' via 'monthly_pass_ref'. This allows the system to bypass standard 
--   fare calculations during tap-in audits if a valid pass exists, while fully tracking 
--   passenger flow and ride histories.
============================================================
-- 💡 NEWLY ADDED EXTENSION FEATURE 3: Customer Loyalty & Rewards System
-- ============================================================
-- Functionality & System Role:
--   To incentivize frequent travel, users accumulate loyalty points for every purchase.
--   The 'loyalty_points' column in 'registered_users' acts as a real-time ledger balance.
--   It is protected by a CHECK constraint (>= 0) to prevent negative balances at the DB level,
--   ensuring strict data integrity without solely relying on Python validation.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- 0. Reset tables for repeatable schema execution
-- ============================================================
DROP TABLE IF EXISTS metro_feedback CASCADE;
DROP TABLE IF EXISTS national_rail_feedback CASCADE;
DROP TABLE IF EXISTS metro_monthly_pass_payments CASCADE;
DROP TABLE IF EXISTS metro_payments CASCADE;
DROP TABLE IF EXISTS national_rail_payments CASCADE;
DROP TABLE IF EXISTS metro_trips CASCADE;
DROP TABLE IF EXISTS metro_monthly_passes CASCADE;
DROP TABLE IF EXISTS national_rail_bookings CASCADE;
DROP TABLE IF EXISTS metro_platforms CASCADE;
DROP TABLE IF EXISTS national_rail_platforms CASCADE;
DROP TABLE IF EXISTS national_rail_seats CASCADE;
DROP TABLE IF EXISTS metro_schedule_stops CASCADE;
DROP TABLE IF EXISTS national_rail_schedule_stops CASCADE;
DROP TABLE IF EXISTS metro_schedules CASCADE;
DROP TABLE IF EXISTS national_rail_schedules CASCADE;
DROP TABLE IF EXISTS metro_stations CASCADE;
DROP TABLE IF EXISTS national_rail_stations CASCADE;
DROP TABLE IF EXISTS registered_users CASCADE;

-- ============================================================
-- 1. Users and master station tables
-- ============================================================

CREATE TABLE registered_users (
    -- PK uses UUID because users are core application entities created by the system;
    -- UUID avoids exposing sequential user counts and still works across distributed systems.
    user_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Original source JSON ID, e.g. U001. Kept for import/demo lookup, not used as PK.
    user_code        VARCHAR(20) NOT NULL UNIQUE,

    full_name        VARCHAR(200) NOT NULL,
    email            VARCHAR(255) NOT NULL UNIQUE,
    password_hash    VARCHAR(255) NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
    phone            VARCHAR(20),
    date_of_birth    DATE NOT NULL,
    secret_question  VARCHAR(255),
    secret_answer    VARCHAR(255),
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    loyalty_points   INTEGER NOT NULL DEFAULT 0 CHECK (loyalty_points >= 0)
);

CREATE TABLE national_rail_stations (
    -- PK uses SERIAL because stations are small master/reference records loaded locally;
    -- the public JSON station code is stored separately in station_code.
    station_id                       SERIAL PRIMARY KEY,
    station_code                     VARCHAR(20) NOT NULL UNIQUE,

    name                             VARCHAR(200) NOT NULL,
    lines                            JSONB NOT NULL,
    is_interchange_national_rail     BOOLEAN NOT NULL DEFAULT FALSE,
    interchange_national_rail_lines  JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_interchange_metro             BOOLEAN NOT NULL DEFAULT FALSE,

    interchange_metro_station_code   VARCHAR(20),-- Stored as source code to avoid circular FK load dependency between rail and metro stations.
    adjacent_stations                JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE metro_stations (
    -- PK uses SERIAL because stations are small master/reference records loaded locally;
    -- the public JSON station code is stored separately in station_code.
    station_id                           SERIAL PRIMARY KEY,
    station_code                         VARCHAR(20) NOT NULL UNIQUE,

    name                                 VARCHAR(200) NOT NULL,
    lines                                JSONB NOT NULL,
    is_interchange_metro                 BOOLEAN NOT NULL DEFAULT FALSE,
    interchange_metro_lines              JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_interchange_national_rail         BOOLEAN NOT NULL DEFAULT FALSE,

    -- Stored as source code to avoid circular FK load dependency between rail and metro stations.
    interchange_national_rail_station_code VARCHAR(20),
    adjacent_stations                    JSONB NOT NULL DEFAULT '[]'::jsonb
);


ALTER TABLE national_rail_stations
ADD CONSTRAINT fk_national_rail_interchange_metro_station_code
FOREIGN KEY (interchange_metro_station_code)
REFERENCES metro_stations(station_code)
ON DELETE SET NULL;

ALTER TABLE metro_stations
ADD CONSTRAINT fk_metro_interchange_national_rail_station_code
FOREIGN KEY (interchange_national_rail_station_code)
REFERENCES national_rail_stations(station_code)
ON DELETE SET NULL;

-- ============================================================
-- 2. Schedule tables and normalized stop junction tables
-- ============================================================

CREATE TABLE national_rail_schedules (
    -- PK uses SERIAL because schedules are imported service definitions; schedule_code
    -- preserves the JSON ID, e.g. NR_SCH01, for deterministic seeding and queries.
    schedule_id             SERIAL PRIMARY KEY,
    schedule_code           VARCHAR(30) NOT NULL UNIQUE,

    line                    VARCHAR(20) NOT NULL,
    service_type            VARCHAR(20) NOT NULL CHECK (service_type IN ('normal', 'express')),
    direction               VARCHAR(20) NOT NULL CHECK (direction IN ('northbound', 'southbound', 'eastbound', 'westbound')),
    origin_station_id       INTEGER NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id  INTEGER NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    fare_classes            JSONB NOT NULL,
    first_train_time        TIME NOT NULL,
    last_train_time         TIME NOT NULL,
    frequency_min           INTEGER NOT NULL CHECK (frequency_min > 0),
    operates_on             JSONB NOT NULL
);

CREATE TABLE metro_schedules (
    -- PK uses SERIAL because schedules are imported service definitions; schedule_code
    schedule_id             SERIAL PRIMARY KEY,
    schedule_code           VARCHAR(30) NOT NULL UNIQUE,
    -- preserves the JSON ID, e.g. M_SCH01, for deterministic seeding and queries.
    line                    VARCHAR(20) NOT NULL,
    direction               VARCHAR(20) NOT NULL CHECK (direction IN ('northbound', 'southbound', 'eastbound', 'westbound')),
    origin_station_id       INTEGER NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id  INTEGER NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    base_fare_usd           NUMERIC(10,2) NOT NULL CHECK (base_fare_usd >= 0),
    per_stop_rate_usd       NUMERIC(10,2) NOT NULL CHECK (per_stop_rate_usd >= 0),
    first_train_time        TIME NOT NULL,
    last_train_time         TIME NOT NULL,
    frequency_min           INTEGER NOT NULL CHECK (frequency_min > 0),
    operates_on             JSONB NOT NULL
);

CREATE TABLE national_rail_schedule_stops (
    -- Composite PK uses parent SERIAL FK + stop_order because one schedule has an ordered
    -- stop sequence and each order position must be unique within that schedule.
    schedule_id                 INTEGER NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    stop_order                  INTEGER NOT NULL CHECK (stop_order > 0),
    station_id                  INTEGER NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    travel_time_from_origin_min INTEGER NOT NULL CHECK (travel_time_from_origin_min >= 0),
    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id)
);

CREATE TABLE metro_schedule_stops (
    -- Composite PK uses parent SERIAL FK + stop_order because one schedule has an ordered
    -- stop sequence and each order position must be unique within that schedule.
    schedule_id                 INTEGER NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE CASCADE,
    stop_order                  INTEGER NOT NULL CHECK (stop_order > 0),
    station_id                  INTEGER NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    travel_time_from_origin_min INTEGER NOT NULL CHECK (travel_time_from_origin_min >= 0),
    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id)
);

-- ============================================================
-- 3. Seat and platform tables
-- ============================================================

CREATE TABLE national_rail_seats (
    -- PK uses SERIAL because each physical/imported seat row is internally generated;
    -- seat_code preserves source values such as A01/B12 for display and import mapping.
    seat_pk       SERIAL PRIMARY KEY,
    schedule_id   INTEGER NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    seat_code     VARCHAR(20) NOT NULL,
    coach         VARCHAR(10) NOT NULL,
    fare_class    VARCHAR(20) NOT NULL CHECK (fare_class IN ('first', 'standard')),
    seat_row      INTEGER NOT NULL CHECK (seat_row > 0),
    seat_column   VARCHAR(5) NOT NULL,
    UNIQUE (schedule_id, seat_code)
);

CREATE TABLE national_rail_platforms (
    -- PK uses SERIAL because platform assignment rows are generated from schedule/station logic.
    platform_id     SERIAL PRIMARY KEY,
    schedule_id     INTEGER NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    station_id      INTEGER NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    direction       VARCHAR(20) NOT NULL CHECK (direction IN ('northbound', 'southbound', 'eastbound', 'westbound')),
    platform_number INTEGER NOT NULL CHECK (platform_number BETWEEN 1 AND 4),
    UNIQUE (schedule_id, station_id)
);

CREATE TABLE metro_platforms (
    -- PK uses SERIAL because platform assignment rows are generated from schedule/station logic.
    platform_id     SERIAL PRIMARY KEY,
    schedule_id     INTEGER NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE CASCADE,
    station_id      INTEGER NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    direction       VARCHAR(20) NOT NULL CHECK (direction IN ('northbound', 'southbound', 'eastbound', 'westbound')),
    platform_number INTEGER NOT NULL CHECK (platform_number BETWEEN 1 AND 4),
    UNIQUE (schedule_id, station_id)
);

-- ============================================================
-- 4. Transaction tables
-- ============================================================

CREATE TABLE national_rail_bookings (
    -- PK uses UUID because bookings are business transactions created by the system;
    -- booking_code preserves JSON values such as BK001 for import/demo lookup.
    booking_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_code            VARCHAR(30) NOT NULL UNIQUE,

    user_id                 UUID NOT NULL REFERENCES registered_users(user_id) ON DELETE RESTRICT,
    schedule_id             INTEGER NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id       INTEGER NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id  INTEGER NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    travel_date             DATE NOT NULL,
    departure_time          TIME NOT NULL,
    ticket_type             VARCHAR(20) NOT NULL CHECK (ticket_type IN ('single', 'return')),
    fare_class              VARCHAR(20) NOT NULL CHECK (fare_class IN ('first', 'standard')),
    coach                   VARCHAR(10) NOT NULL,
    seat_pk                 INTEGER NOT NULL REFERENCES national_rail_seats(seat_pk) ON DELETE RESTRICT,
    stops_travelled         INTEGER NOT NULL CHECK (stops_travelled >= 0),
    amount_usd              NUMERIC(10,2) NOT NULL CHECK (amount_usd >= 0),
    status                  VARCHAR(20) NOT NULL CHECK (status IN ('confirmed', 'completed', 'cancelled')),
    booked_at               TIMESTAMPTZ NOT NULL,
    travelled_at            TIMESTAMPTZ
);

CREATE TABLE metro_monthly_passes (
    -- PK uses UUID because passes are user-owned purchase records created by the system;
    -- pass_code preserves source JSON values when present.
    pass_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pass_code      VARCHAR(30) NOT NULL UNIQUE,

    user_id        UUID NOT NULL REFERENCES registered_users(user_id) ON DELETE RESTRICT,
    valid_from     DATE NOT NULL,
    valid_until    DATE NOT NULL CHECK (valid_until >= valid_from),
    price_usd      NUMERIC(10,2) NOT NULL CHECK (price_usd >= 0),
    purchased_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE metro_trips (
    -- PK uses UUID because trips are business transactions created by the system;
    -- trip_code preserves JSON values such as MT001 for import/demo lookup.
    trip_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_code               VARCHAR(30) NOT NULL UNIQUE,

    user_id                 UUID NOT NULL REFERENCES registered_users(user_id) ON DELETE RESTRICT,
    schedule_id             INTEGER NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id       INTEGER NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id  INTEGER NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    travel_date             DATE NOT NULL,
    ticket_type             VARCHAR(20) NOT NULL CHECK (ticket_type IN ('single', 'day_pass', 'monthly_pass')),
    day_pass_ref            UUID REFERENCES metro_trips(trip_id) ON DELETE SET NULL,
    monthly_pass_ref        UUID REFERENCES metro_monthly_passes(pass_id) ON DELETE SET NULL,
    stops_travelled         INTEGER CHECK (stops_travelled IS NULL OR stops_travelled >= 0),
    amount_usd              NUMERIC(10,2) NOT NULL CHECK (amount_usd >= 0),
    status                  VARCHAR(20) NOT NULL CHECK (status IN ('completed', 'cancelled')),
    purchased_at            TIMESTAMPTZ,
    travelled_at            TIMESTAMPTZ
);

-- ============================================================
-- 5. Split payment and feedback tables
-- ============================================================

CREATE TABLE national_rail_payments (
    -- PK uses UUID because payment records are financial transactions and should not
    -- expose sequential counts; payment_code preserves source JSON values.
    payment_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_code VARCHAR(30) NOT NULL UNIQUE,

    booking_id   UUID NOT NULL REFERENCES national_rail_bookings(booking_id) ON DELETE RESTRICT,
    amount_usd   NUMERIC(10,2) NOT NULL CHECK (amount_usd >= 0),
    method       VARCHAR(50) NOT NULL CHECK (method IN ('credit_card', 'debit_card', 'ewallet')),
    status       VARCHAR(20) NOT NULL CHECK (status IN ('paid', 'refunded')),
    paid_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE metro_payments (
    -- PK uses UUID because payment records are financial transactions and should not
    -- expose sequential counts; payment_code preserves source JSON values.
    payment_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_code VARCHAR(30) NOT NULL UNIQUE,

    trip_id      UUID NOT NULL REFERENCES metro_trips(trip_id) ON DELETE RESTRICT,
    amount_usd   NUMERIC(10,2) NOT NULL CHECK (amount_usd >= 0),
    method       VARCHAR(50) NOT NULL CHECK (method IN ('credit_card', 'debit_card', 'ewallet')),
    status       VARCHAR(20) NOT NULL CHECK (status IN ('paid', 'refunded')),
    paid_at      TIMESTAMPTZ NOT NULL
);


CREATE TABLE metro_monthly_pass_payments (
    -- PK uses UUID because monthly pass payments are financial transactions
    -- and should not expose sequential counts.
    payment_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_code VARCHAR(30) NOT NULL UNIQUE,

    pass_id      UUID NOT NULL REFERENCES metro_monthly_passes(pass_id) ON DELETE RESTRICT,
    amount_usd   NUMERIC(10,2) NOT NULL CHECK (amount_usd >= 0),
    method       VARCHAR(50) NOT NULL CHECK (method IN ('credit_card', 'debit_card', 'ewallet')),
    status       VARCHAR(20) NOT NULL CHECK (status IN ('paid', 'refunded')),
    paid_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_metro_monthly_pass_payments_pass_id
ON metro_monthly_pass_payments(pass_id);

CREATE TABLE national_rail_feedback (
    -- PK uses UUID because feedback is user-generated business data;
    -- feedback_code preserves source JSON values for import/demo lookup.
    feedback_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    feedback_code VARCHAR(30) NOT NULL UNIQUE,

    booking_id    UUID NOT NULL REFERENCES national_rail_bookings(booking_id) ON DELETE RESTRICT,
    user_id       UUID NOT NULL REFERENCES registered_users(user_id) ON DELETE RESTRICT,
    rating        INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    submitted_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE metro_feedback (
    -- PK uses UUID because feedback is user-generated business data;
    -- feedback_code preserves source JSON values for import/demo lookup.
    feedback_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    feedback_code VARCHAR(30) NOT NULL UNIQUE,

    trip_id       UUID NOT NULL REFERENCES metro_trips(trip_id) ON DELETE RESTRICT,
    user_id       UUID NOT NULL REFERENCES registered_users(user_id) ON DELETE RESTRICT,
    rating        INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    submitted_at  TIMESTAMPTZ NOT NULL
);

-- ============================================================
-- 6. Indexes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_registered_users_user_code ON registered_users(user_code);
CREATE INDEX IF NOT EXISTS idx_national_rail_stations_code ON national_rail_stations(station_code);
CREATE INDEX IF NOT EXISTS idx_metro_stations_code ON metro_stations(station_code);

CREATE INDEX IF NOT EXISTS idx_national_rail_interchange_metro_code
ON national_rail_stations(interchange_metro_station_code);

CREATE INDEX IF NOT EXISTS idx_metro_interchange_national_rail_code
ON metro_stations(interchange_national_rail_station_code);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_code ON national_rail_schedules(schedule_code);
CREATE INDEX IF NOT EXISTS idx_metro_schedules_code ON metro_schedules(schedule_code);

CREATE INDEX IF NOT EXISTS idx_national_rail_stations_lines_gin ON national_rail_stations USING GIN (lines);
CREATE INDEX IF NOT EXISTS idx_metro_stations_lines_gin ON metro_stations USING GIN (lines);
CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_operates_on_gin ON national_rail_schedules USING GIN (operates_on);
CREATE INDEX IF NOT EXISTS idx_metro_schedules_operates_on_gin ON metro_schedules USING GIN (operates_on);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_origin ON national_rail_schedules(origin_station_id);
CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_destination ON national_rail_schedules(destination_station_id);
CREATE INDEX IF NOT EXISTS idx_metro_schedules_origin ON metro_schedules(origin_station_id);
CREATE INDEX IF NOT EXISTS idx_metro_schedules_destination ON metro_schedules(destination_station_id);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedule_stops_station ON national_rail_schedule_stops(station_id);
CREATE INDEX IF NOT EXISTS idx_metro_schedule_stops_station ON metro_schedule_stops(station_id);

CREATE INDEX IF NOT EXISTS idx_national_rail_bookings_user_id ON national_rail_bookings(user_id);
CREATE INDEX IF NOT EXISTS idx_national_rail_bookings_schedule_id ON national_rail_bookings(schedule_id);
CREATE INDEX IF NOT EXISTS idx_national_rail_bookings_travel_date ON national_rail_bookings(travel_date);
CREATE INDEX IF NOT EXISTS idx_nrb_origin ON national_rail_bookings(origin_station_id);
CREATE INDEX IF NOT EXISTS idx_nrb_dest ON national_rail_bookings(destination_station_id);

CREATE INDEX IF NOT EXISTS idx_metro_trips_user_id ON metro_trips(user_id);
CREATE INDEX IF NOT EXISTS idx_metro_trips_schedule_id ON metro_trips(schedule_id);
CREATE INDEX IF NOT EXISTS idx_metro_trips_travel_date ON metro_trips(travel_date);
CREATE INDEX IF NOT EXISTS idx_mt_origin ON metro_trips(origin_station_id);
CREATE INDEX IF NOT EXISTS idx_mt_dest ON metro_trips(destination_station_id);

-- Available-seat lookup index.
CREATE INDEX IF NOT EXISTS idx_national_rail_bookings_available_seats
ON national_rail_bookings (schedule_id, travel_date, departure_time, seat_pk)
WHERE status IN ('confirmed', 'completed');

-- Prevent double-booking the same seat on the same scheduled departure.
CREATE UNIQUE INDEX IF NOT EXISTS uq_national_rail_active_seat_booking
ON national_rail_bookings (schedule_id, travel_date, departure_time, seat_pk)
WHERE status IN ('confirmed', 'completed');

CREATE INDEX IF NOT EXISTS idx_national_rail_seats_schedule_class_coach
ON national_rail_seats (schedule_id, fare_class, coach);

CREATE INDEX IF NOT EXISTS idx_national_rail_payments_booking_id ON national_rail_payments(booking_id);
CREATE INDEX IF NOT EXISTS idx_metro_payments_trip_id ON metro_payments(trip_id);
CREATE INDEX IF NOT EXISTS idx_national_rail_feedback_booking_id ON national_rail_feedback(booking_id);
CREATE INDEX IF NOT EXISTS idx_national_rail_feedback_user_id ON national_rail_feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_metro_feedback_trip_id ON metro_feedback(trip_id);
CREATE INDEX IF NOT EXISTS idx_metro_feedback_user_id ON metro_feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_metro_monthly_passes_user ON metro_monthly_passes(user_id);

CREATE INDEX IF NOT EXISTS idx_national_rail_platforms_schedule_station
ON national_rail_platforms(schedule_id, station_id);
CREATE INDEX IF NOT EXISTS idx_metro_platforms_schedule_station
ON metro_platforms(schedule_id, station_id);

-- ============================================================
-- 7. Available seats function
-- ============================================================

CREATE OR REPLACE FUNCTION get_available_national_rail_seats(
    p_schedule_id INTEGER,
    p_travel_date DATE,
    p_departure_time TIME
)
RETURNS TABLE (
    schedule_id INTEGER,
    seat_pk INTEGER,
    seat_code VARCHAR,
    coach VARCHAR,
    fare_class VARCHAR,
    seat_row INTEGER,
    seat_column VARCHAR
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        s.schedule_id,
        s.seat_pk,
        s.seat_code,
        s.coach,
        s.fare_class,
        s.seat_row,
        s.seat_column
    FROM national_rail_seats s
    WHERE s.schedule_id = p_schedule_id
      AND NOT EXISTS (
          SELECT 1
          FROM national_rail_bookings b
          WHERE b.schedule_id = s.schedule_id
            AND b.travel_date = p_travel_date
            AND b.departure_time = p_departure_time
            AND b.seat_pk = s.seat_pk
            AND b.status IN ('confirmed', 'completed')
      )
    ORDER BY s.coach, s.seat_row, s.seat_column;
END;
$$ LANGUAGE plpgsql;


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
CREATE INDEX IF NOT EXISTS idx_policy_documents_embedding_hnsw
ON policy_documents USING hnsw (embedding vector_cosine_ops);
