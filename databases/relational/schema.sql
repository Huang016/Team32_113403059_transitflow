-- ============================================================
-- TransitFlow PostgreSQL Schema — full revised version
-- Changes included:
-- 1) registered_users.password -> password_hash with Argon2id format check
-- 2) Removed circular FK constraints between metro_stations and national_rail_stations
--    for interchange columns, so seed_postgres.py can load station JSON safely.
-- 3) Added DROP TABLE IF EXISTS and CREATE INDEX IF NOT EXISTS so this file can be rerun.
-- 4) Added available-seat lookup / anti-double-booking indexes for national rail bookings.
-- 5) Kept secret_answer as plain text; only password is hashed.
-- 6) Added get_available_national_rail_seats() function.
-- Seed data is loaded separately by: python skeleton/seed_postgres.py
-- ============================================================

-- ============================================================
-- 0. Reset tables for repeatable schema execution
-- ============================================================
DROP TABLE IF EXISTS metro_feedback CASCADE;
DROP TABLE IF EXISTS national_rail_feedback CASCADE;
DROP TABLE IF EXISTS metro_payments CASCADE;
DROP TABLE IF EXISTS national_rail_payments CASCADE;
DROP TABLE IF EXISTS metro_trips CASCADE;
DROP TABLE IF EXISTS national_rail_bookings CASCADE;
DROP TABLE IF EXISTS national_rail_seats CASCADE;
DROP TABLE IF EXISTS metro_schedules CASCADE;
DROP TABLE IF EXISTS national_rail_schedules CASCADE;
DROP TABLE IF EXISTS metro_stations CASCADE;
DROP TABLE IF EXISTS national_rail_stations CASCADE;
DROP TABLE IF EXISTS registered_users CASCADE;
DROP TABLE IF EXISTS policy_documents CASCADE;

-- ============================================================
-- 1. Independent master tables
-- ============================================================

CREATE TABLE registered_users (
    user_id          VARCHAR(10)  PRIMARY KEY,
    full_name        VARCHAR(200) NOT NULL,
    email            VARCHAR(255) NOT NULL UNIQUE,
    -- JSON source uses "password", but seed_postgres.py should hash it first.
    -- Store only Argon2id hashes, never plain text passwords.
    password_hash    VARCHAR(255) NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
    phone            VARCHAR(20),
    date_of_birth    DATE NOT NULL,
    secret_question  VARCHAR(255),
    secret_answer    VARCHAR(255) ,
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active        BOOLEAN NOT NULL DEFAULT TRUE
);

-- National rail stations. Arrays / nested route-neighbour data are kept as JSONB
-- because the source JSON stores them as arrays of strings/objects.
CREATE TABLE national_rail_stations (
    station_id                         VARCHAR(10) PRIMARY KEY,
    name                               VARCHAR(200) NOT NULL,
    lines                              JSONB NOT NULL,
    is_interchange_national_rail       BOOLEAN NOT NULL DEFAULT FALSE,
    interchange_national_rail_lines    JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_interchange_metro               BOOLEAN NOT NULL DEFAULT FALSE,
    -- No FK here to avoid circular seed dependency with metro_stations.
    interchange_metro_station_id       VARCHAR(10),
    adjacent_stations                  JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE metro_stations (
    station_id                             VARCHAR(10) PRIMARY KEY,
    name                                   VARCHAR(200) NOT NULL,
    lines                                  JSONB NOT NULL,
    is_interchange_metro                   BOOLEAN NOT NULL DEFAULT FALSE,
    interchange_metro_lines                JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_interchange_national_rail           BOOLEAN NOT NULL DEFAULT FALSE,
    -- No FK here to avoid circular seed dependency with national_rail_stations.
    interchange_national_rail_station_id   VARCHAR(10),
    adjacent_stations                      JSONB NOT NULL DEFAULT '[]'::jsonb
);

-- ============================================================
-- 2. Schedule and seat tables
-- ============================================================

CREATE TABLE national_rail_schedules (
    schedule_id                 VARCHAR(20) PRIMARY KEY,
    line                        VARCHAR(10) NOT NULL,
    service_type                VARCHAR(20) NOT NULL CHECK (service_type IN ('normal', 'express')),
    direction                   VARCHAR(20) NOT NULL CHECK (direction IN ('northbound', 'southbound', 'eastbound', 'westbound')),
    origin_station_id           VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id      VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    stops_in_order              JSONB NOT NULL,
    travel_time_from_origin_min JSONB NOT NULL,
    fare_classes                JSONB NOT NULL,
    first_train_time            TIME NOT NULL,
    last_train_time             TIME NOT NULL,
    frequency_min               INTEGER NOT NULL CHECK (frequency_min > 0),
    operates_on                 JSONB NOT NULL
);

CREATE TABLE metro_schedules (
    schedule_id                 VARCHAR(20) PRIMARY KEY,
    line                        VARCHAR(10) NOT NULL,
    direction                   VARCHAR(20) NOT NULL CHECK (direction IN ('northbound', 'southbound', 'eastbound', 'westbound')),
    origin_station_id           VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id      VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    stops_in_order              JSONB NOT NULL,
    travel_time_from_origin_min JSONB NOT NULL,
    base_fare_usd               NUMERIC(10,2) NOT NULL CHECK (base_fare_usd >= 0),
    per_stop_rate_usd           NUMERIC(10,2) NOT NULL CHECK (per_stop_rate_usd >= 0),
    first_train_time            TIME NOT NULL,
    last_train_time             TIME NOT NULL,
    frequency_min               INTEGER NOT NULL CHECK (frequency_min > 0),
    operates_on                 JSONB NOT NULL
);

-- The seat-layout JSON is nested, but tutorial recommends flattening seats for easy querying.
CREATE TABLE national_rail_seats (
    schedule_id   VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    seat_id       VARCHAR(10) NOT NULL,
    coach         VARCHAR(5) NOT NULL,
    fare_class    VARCHAR(20) NOT NULL CHECK (fare_class IN ('first', 'standard')),
    seat_row      INTEGER NOT NULL CHECK (seat_row > 0),
    seat_column   VARCHAR(5) NOT NULL,
    PRIMARY KEY (schedule_id, seat_id)
);

-- ============================================================
-- 3. Transaction tables
-- ============================================================

CREATE TABLE national_rail_bookings (
    booking_id              VARCHAR(20) PRIMARY KEY,
    user_id                 VARCHAR(10) NOT NULL REFERENCES registered_users(user_id) ON DELETE RESTRICT,
    schedule_id             VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id       VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id  VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    travel_date             DATE NOT NULL,
    departure_time          TIME NOT NULL,
    ticket_type             VARCHAR(20) NOT NULL CHECK (ticket_type IN ('single', 'return')),
    fare_class              VARCHAR(20) NOT NULL CHECK (fare_class IN ('first', 'standard')),
    coach                   VARCHAR(5) NOT NULL,
    seat_id                 VARCHAR(10) NOT NULL,
    stops_travelled         INTEGER NOT NULL CHECK (stops_travelled >= 0),
    amount_usd              NUMERIC(10,2) NOT NULL CHECK (amount_usd >= 0),
    status                  VARCHAR(20) NOT NULL CHECK (status IN ('confirmed', 'completed', 'cancelled')),
    booked_at               TIMESTAMPTZ NOT NULL,
    travelled_at            TIMESTAMPTZ,
    FOREIGN KEY (schedule_id, seat_id)
        REFERENCES national_rail_seats(schedule_id, seat_id)
        ON DELETE RESTRICT
);

CREATE TABLE metro_trips (
    trip_id                 VARCHAR(20) PRIMARY KEY,
    user_id                 VARCHAR(10) NOT NULL REFERENCES registered_users(user_id) ON DELETE RESTRICT,
    schedule_id             VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id       VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id  VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    travel_date             DATE NOT NULL,
    ticket_type             VARCHAR(20) NOT NULL CHECK (ticket_type IN ('single', 'day_pass')),
    day_pass_ref            VARCHAR(20) REFERENCES metro_trips(trip_id) ON DELETE SET NULL,
    stops_travelled         INTEGER CHECK (stops_travelled IS NULL OR stops_travelled >= 0),
    amount_usd              NUMERIC(10,2) NOT NULL CHECK (amount_usd >= 0),
    status                  VARCHAR(20) NOT NULL CHECK (status IN ('completed', 'cancelled')),
    purchased_at            TIMESTAMPTZ,
    travelled_at            TIMESTAMPTZ
);

-- ============================================================
-- 4. Split payment and feedback tables, matching the ERD
-- ============================================================
-- The original payments / feedback JSON uses a field named "booking_id" for both
-- national rail bookings (BKxxx) and metro trips (MTxxx).
-- To match the ERD, seed_postgres.py should route rows by prefix:
--   BKxxx -> national_rail_payments / national_rail_feedback.booking_id
--   MTxxx -> metro_payments / metro_feedback.trip_id

CREATE TABLE national_rail_payments (
    payment_id       VARCHAR(15) PRIMARY KEY,
    booking_id       VARCHAR(20) NOT NULL REFERENCES national_rail_bookings(booking_id) ON DELETE RESTRICT,
    amount_usd       NUMERIC(10,2) NOT NULL CHECK (amount_usd >= 0),
    method           VARCHAR(50) NOT NULL CHECK (method IN ('credit_card', 'debit_card', 'ewallet')),
    status           VARCHAR(20) NOT NULL CHECK (status IN ('paid', 'refunded')),
    paid_at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE metro_payments (
    payment_id       VARCHAR(15) PRIMARY KEY,
    trip_id          VARCHAR(20) NOT NULL REFERENCES metro_trips(trip_id) ON DELETE RESTRICT,
    amount_usd       NUMERIC(10,2) NOT NULL CHECK (amount_usd >= 0),
    method           VARCHAR(50) NOT NULL CHECK (method IN ('credit_card', 'debit_card', 'ewallet')),
    status           VARCHAR(20) NOT NULL CHECK (status IN ('paid', 'refunded')),
    paid_at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE national_rail_feedback (
    feedback_id      VARCHAR(15) PRIMARY KEY,
    booking_id       VARCHAR(20) NOT NULL REFERENCES national_rail_bookings(booking_id) ON DELETE RESTRICT,
    user_id          VARCHAR(10) NOT NULL REFERENCES registered_users(user_id) ON DELETE RESTRICT,
    rating           INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment          TEXT,
    submitted_at     TIMESTAMPTZ NOT NULL
);

CREATE TABLE metro_feedback (
    feedback_id      VARCHAR(15) PRIMARY KEY,
    trip_id          VARCHAR(20) NOT NULL REFERENCES metro_trips(trip_id) ON DELETE RESTRICT,
    user_id          VARCHAR(10) NOT NULL REFERENCES registered_users(user_id) ON DELETE RESTRICT,
    rating           INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment          TEXT,
    submitted_at     TIMESTAMPTZ NOT NULL
);

-- ============================================================
-- 5. Indexes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_national_rail_stations_lines_gin ON national_rail_stations USING GIN (lines);
CREATE INDEX IF NOT EXISTS idx_metro_stations_lines_gin ON metro_stations USING GIN (lines);
CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_operates_on_gin ON national_rail_schedules USING GIN (operates_on);
CREATE INDEX IF NOT EXISTS idx_metro_schedules_operates_on_gin ON metro_schedules USING GIN (operates_on);

CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_origin ON national_rail_schedules(origin_station_id);
CREATE INDEX IF NOT EXISTS idx_national_rail_schedules_destination ON national_rail_schedules(destination_station_id);
CREATE INDEX IF NOT EXISTS idx_metro_schedules_origin ON metro_schedules(origin_station_id);
CREATE INDEX IF NOT EXISTS idx_metro_schedules_destination ON metro_schedules(destination_station_id);

CREATE INDEX IF NOT EXISTS idx_national_rail_bookings_user_id ON national_rail_bookings(user_id);
CREATE INDEX IF NOT EXISTS idx_national_rail_bookings_schedule_id ON national_rail_bookings(schedule_id);
CREATE INDEX IF NOT EXISTS idx_national_rail_bookings_travel_date ON national_rail_bookings(travel_date);
CREATE INDEX IF NOT EXISTS idx_nrb_origin ON national_rail_bookings(origin_station_id);
CREATE INDEX IF NOT EXISTS idx_nrb_dest ON national_rail_bookings(destination_station_id);
CREATE INDEX IF NOT EXISTS idx_mt_origin ON metro_trips(origin_station_id);
CREATE INDEX IF NOT EXISTS idx_mt_dest ON metro_trips(destination_station_id);
-- Available-seat lookup index:
-- Used by queries that find seats in national_rail_seats that are NOT occupied
-- by confirmed / completed bookings for the same schedule + date + departure time.
CREATE INDEX IF NOT EXISTS idx_national_rail_bookings_available_seats
ON national_rail_bookings (schedule_id, travel_date, departure_time, seat_id)
WHERE status IN ('confirmed', 'completed');

-- Prevent double-booking the same seat on the same scheduled departure.
-- Cancelled bookings are excluded, so a cancelled seat can be sold again.
CREATE UNIQUE INDEX IF NOT EXISTS uq_national_rail_active_seat_booking
ON national_rail_bookings (schedule_id, travel_date, departure_time, seat_id)
WHERE status IN ('confirmed', 'completed');

-- Helps seat-filtering screens such as first/standard class or coach filters.
CREATE INDEX IF NOT EXISTS idx_national_rail_seats_schedule_class_coach
ON national_rail_seats (schedule_id, fare_class, coach);

CREATE INDEX IF NOT EXISTS idx_metro_trips_user_id ON metro_trips(user_id);
CREATE INDEX IF NOT EXISTS idx_metro_trips_schedule_id ON metro_trips(schedule_id);
CREATE INDEX IF NOT EXISTS idx_metro_trips_travel_date ON metro_trips(travel_date);

CREATE INDEX IF NOT EXISTS idx_national_rail_payments_booking_id ON national_rail_payments(booking_id);
CREATE INDEX IF NOT EXISTS idx_metro_payments_trip_id ON metro_payments(trip_id);
CREATE INDEX IF NOT EXISTS idx_national_rail_feedback_booking_id ON national_rail_feedback(booking_id);
CREATE INDEX IF NOT EXISTS idx_national_rail_feedback_user_id ON national_rail_feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_metro_feedback_trip_id ON metro_feedback(trip_id);
CREATE INDEX IF NOT EXISTS idx_metro_feedback_user_id ON metro_feedback(user_id);


-- ============================================================
-- 6. Available seats function
-- ============================================================
-- Calculates available seats dynamically from:
--   national_rail_seats - confirmed/completed national_rail_bookings
-- Usage example:
-- SELECT *
-- FROM get_available_national_rail_seats('NR_SCH01', DATE '2026-04-02', TIME '07:00');

CREATE OR REPLACE FUNCTION get_available_national_rail_seats(
    p_schedule_id VARCHAR,
    p_travel_date DATE,
    p_departure_time TIME
)
RETURNS TABLE (
    schedule_id VARCHAR,
    seat_id VARCHAR,
    coach VARCHAR,
    fare_class VARCHAR,
    seat_row INTEGER,
    seat_column VARCHAR
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        s.schedule_id,
        s.seat_id,
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
            AND b.seat_id = s.seat_id
            AND b.status IN ('confirmed', 'completed')
      )
    ORDER BY s.coach, s.seat_row, s.seat_column;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 7. VECTOR SCHEMA  (RAG / Help Desk)do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE policy_documents (
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
