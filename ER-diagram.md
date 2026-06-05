erDiagram
    %% ==========================================
    %% 1. Users and AI Policy Retrieval Vector
    %% ==========================================

    registered_users {
        VARCHAR user_id PK
        VARCHAR full_name "NOT NULL"
        VARCHAR email UK "NOT NULL"
        VARCHAR password_hash "NOT NULL, Argon2id"
        VARCHAR phone
        DATE date_of_birth "NOT NULL"
        VARCHAR secret_question
        VARCHAR secret_answer
        TIMESTAMPTZ registered_at "NOT NULL DEFAULT NOW"
        BOOLEAN is_active "NOT NULL DEFAULT TRUE"
        INTEGER loyalty_points "NOT NULL DEFAULT 0"
    }

    policy_documents {
        SERIAL id PK
        VARCHAR title "NOT NULL"
        VARCHAR category "NOT NULL"
        TEXT content "NOT NULL"
        vector embedding "768 dimensions"
        VARCHAR source_file
        TIMESTAMPTZ created_at "DEFAULT NOW"
    }

    %% ==========================================
    %% 2. Infrastructure: Stations and Schedules
    %% ==========================================

    national_rail_stations {
        VARCHAR station_id PK
        VARCHAR name "NOT NULL"
        JSONB lines "NOT NULL"
        BOOLEAN is_interchange_national_rail "DEFAULT FALSE"
        JSONB interchange_national_rail_lines "DEFAULT []"
        BOOLEAN is_interchange_metro "DEFAULT FALSE"
        VARCHAR interchange_metro_station_id
        JSONB adjacent_stations "DEFAULT []"
    }

    metro_stations {
        VARCHAR station_id PK
        VARCHAR name "NOT NULL"
        JSONB lines "NOT NULL"
        BOOLEAN is_interchange_metro "DEFAULT FALSE"
        JSONB interchange_metro_lines "DEFAULT []"
        BOOLEAN is_interchange_national_rail "DEFAULT FALSE"
        VARCHAR interchange_national_rail_station_id
        JSONB adjacent_stations "DEFAULT []"
    }

    national_rail_schedules {
        VARCHAR schedule_id PK
        VARCHAR line "NOT NULL"
        VARCHAR service_type "normal / express"
        VARCHAR direction "northbound / southbound / eastbound / westbound"
        VARCHAR origin_station_id FK
        VARCHAR destination_station_id FK
        JSONB stops_in_order "NOT NULL"
        JSONB travel_time_from_origin_min "NOT NULL"
        JSONB fare_classes "NOT NULL"
        TIME first_train_time "NOT NULL"
        TIME last_train_time "NOT NULL"
        INTEGER frequency_min "NOT NULL"
        JSONB operates_on "NOT NULL"
    }

    metro_schedules {
        VARCHAR schedule_id PK
        VARCHAR line "NOT NULL"
        VARCHAR direction "northbound / southbound / eastbound / westbound"
        VARCHAR origin_station_id FK
        VARCHAR destination_station_id FK
        JSONB stops_in_order "NOT NULL"
        JSONB travel_time_from_origin_min "NOT NULL"
        DECIMAL base_fare_usd "10,2 NOT NULL"
        DECIMAL per_stop_rate_usd "10,2 NOT NULL"
        TIME first_train_time "NOT NULL"
        TIME last_train_time "NOT NULL"
        INTEGER frequency_min "NOT NULL"
        JSONB operates_on "NOT NULL"
    }

    %% ==========================================
    %% 3. Seats and Platforms
    %% ==========================================

    national_rail_seats {
        VARCHAR schedule_id PK "FK"
        VARCHAR seat_id PK
        VARCHAR coach "NOT NULL"
        VARCHAR fare_class "first / standard"
        INTEGER seat_row "NOT NULL"
        VARCHAR seat_column "NOT NULL"
    }

    national_rail_platforms {
        VARCHAR platform_id PK
        VARCHAR schedule_id FK
        VARCHAR station_id FK
        VARCHAR direction "northbound / southbound / eastbound / westbound"
        INTEGER platform_number "1-4"
    }

    metro_platforms {
        VARCHAR platform_id PK
        VARCHAR schedule_id FK
        VARCHAR station_id FK
        VARCHAR direction "northbound / southbound / eastbound / westbound"
        INTEGER platform_number "1-4"
    }

    %% ==========================================
    %% 4. National Rail Bookings and Metro Trips
    %% ==========================================

    national_rail_bookings {
        VARCHAR booking_id PK
        VARCHAR user_id FK
        VARCHAR schedule_id FK
        VARCHAR origin_station_id FK
        VARCHAR destination_station_id FK
        DATE travel_date "NOT NULL"
        TIME departure_time "NOT NULL"
        VARCHAR ticket_type "single / return"
        VARCHAR fare_class "first / standard"
        VARCHAR coach "NOT NULL"
        VARCHAR seat_id "NOT NULL"
        INTEGER stops_travelled "NOT NULL"
        DECIMAL amount_usd "10,2 NOT NULL"
        VARCHAR status "confirmed / completed / cancelled"
        TIMESTAMPTZ booked_at "NOT NULL"
        TIMESTAMPTZ travelled_at
    }

    metro_monthly_passes {
        VARCHAR pass_id PK
        VARCHAR user_id FK
        DATE valid_from "NOT NULL"
        DATE valid_until "NOT NULL"
        DECIMAL price_usd "10,2 NOT NULL"
        TIMESTAMPTZ purchased_at "NOT NULL DEFAULT NOW"
    }

    metro_trips {
        VARCHAR trip_id PK
        VARCHAR user_id FK
        VARCHAR schedule_id FK
        VARCHAR origin_station_id FK
        VARCHAR destination_station_id FK
        DATE travel_date "NOT NULL"
        VARCHAR ticket_type "single / day_pass / monthly_pass"
        VARCHAR day_pass_ref FK
        VARCHAR monthly_pass_ref FK
        INTEGER stops_travelled
        DECIMAL amount_usd "10,2 NOT NULL"
        VARCHAR status "completed / cancelled"
        TIMESTAMPTZ purchased_at
        TIMESTAMPTZ travelled_at
    }

    %% ==========================================
    %% 5. Payments and Feedback
    %% ==========================================

    national_rail_payments {
        VARCHAR payment_id PK
        VARCHAR booking_id FK
        DECIMAL amount_usd "10,2 NOT NULL"
        VARCHAR method "credit_card / debit_card / ewallet"
        VARCHAR status "paid / refunded"
        TIMESTAMPTZ paid_at "NOT NULL"
    }

    metro_payments {
        VARCHAR payment_id PK
        VARCHAR trip_id FK
        DECIMAL amount_usd "10,2 NOT NULL"
        VARCHAR method "credit_card / debit_card / ewallet"
        VARCHAR status "paid / refunded"
        TIMESTAMPTZ paid_at "NOT NULL"
    }

    national_rail_feedback {
        VARCHAR feedback_id PK
        VARCHAR booking_id FK
        VARCHAR user_id FK
        INTEGER rating "CHECK 1-5"
        TEXT comment
        TIMESTAMPTZ submitted_at "NOT NULL"
    }

    metro_feedback {
        VARCHAR feedback_id PK
        VARCHAR trip_id FK
        VARCHAR user_id FK
        INTEGER rating "CHECK 1-5"
        TEXT comment
        TIMESTAMPTZ submitted_at "NOT NULL"
    }

    %% ==========================================
    %% Relationships
    %% ==========================================

    %% User relationships
    registered_users ||--o{ national_rail_bookings : "places"
    registered_users ||--o{ metro_trips : "takes"
    registered_users ||--o{ metro_monthly_passes : "buys"
    registered_users ||--o{ national_rail_feedback : "submits"
    registered_users ||--o{ metro_feedback : "submits"

    %% National rail schedules and stations
    national_rail_stations ||--o{ national_rail_schedules : "origin_of"
    national_rail_stations ||--o{ national_rail_schedules : "dest_of"
    national_rail_schedules ||--o{ national_rail_seats : "has_seats"
    national_rail_schedules ||--o{ national_rail_platforms : "uses_platform"
    national_rail_stations ||--o{ national_rail_platforms : "has_platform"

    %% Metro schedules and stations
    metro_stations ||--o{ metro_schedules : "origin_of"
    metro_stations ||--o{ metro_schedules : "dest_of"
    metro_schedules ||--o{ metro_platforms : "uses_platform"
    metro_stations ||--o{ metro_platforms : "has_platform"

    %% National rail bookings
    national_rail_schedules ||--o{ national_rail_bookings : "used_by"
    national_rail_stations ||--o{ national_rail_bookings : "origin_of"
    national_rail_stations ||--o{ national_rail_bookings : "dest_of"
    national_rail_seats ||--o{ national_rail_bookings : "reserved_by"

    %% Metro trips
    metro_schedules ||--o{ metro_trips : "used_by"
    metro_stations ||--o{ metro_trips : "origin_of"
    metro_stations ||--o{ metro_trips : "dest_of"
    metro_trips ||--o| metro_trips : "day_pass_ref"
    metro_monthly_passes ||--o{ metro_trips : "used_by"

    %% Payments
    national_rail_bookings ||--o{ national_rail_payments : "has_payment"
    metro_trips ||--o{ metro_payments : "has_payment"

    %% Feedback
    national_rail_bookings ||--o{ national_rail_feedback : "has_feedback"
    metro_trips ||--o{ metro_feedback : "has_feedback"