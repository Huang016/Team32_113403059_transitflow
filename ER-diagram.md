```mermaid
erDiagram
    %% 1. 使用者與 AI 政策檢索 (Vector)
    registered_users {
        VARCHAR user_id PK
        VARCHAR full_name "NOT NULL"
        VARCHAR email UK "NOT NULL"
        VARCHAR password_hash
        VARCHAR phone
        DATE date_of_birth "NOT NULL"
        VARCHAR secret_question
        VARCHAR secret_answer
        TIMESTAMPTZ registered_at "NOT NULL"
        BOOLEAN is_active "NOT NULL DEFAULT TRUE"
    }

    policy_documents {
        SERIAL id PK
        VARCHAR title "NOT NULL"
        VARCHAR category "NOT NULL"
        TEXT content "NOT NULL"
        vector embedding "768 維度"
        VARCHAR source_file
        TIMESTAMPTZ created_at
    }

    %% 2. 基礎建設 (車站與時刻表)
    national_rail_stations {
        VARCHAR station_id PK
        VARCHAR name "NOT NULL"
        JSONB lines 
        BOOLEAN is_interchange_national_rail
        JSONB interchange_national_rail_lines
        BOOLEAN is_interchange_metro
        VARCHAR interchange_metro_station_id
    }

    metro_stations {
        VARCHAR station_id PK
        VARCHAR name "NOT NULL"
        JSONB lines 
        BOOLEAN is_interchange_metro
        JSONB interchange_metro_lines
        BOOLEAN is_interchange_national_rail
        VARCHAR interchange_national_rail_station_id
    }

    national_rail_schedules {
        VARCHAR schedule_id PK
        VARCHAR line "NOT NULL"
        VARCHAR service_type "normal / express"
        VARCHAR direction
        VARCHAR origin_station_id FK
        VARCHAR destination_station_id FK
        JSONB stops_in_order 
        TIME first_train_time
        TIME last_train_time
        JSONB travel_time_from_origin_min 
        JSONB fare_classes 
        INTEGER frequency_min
        JSONB operates_on 
    }

    metro_schedules {
        VARCHAR schedule_id PK
        VARCHAR line "NOT NULL"
        VARCHAR direction
        VARCHAR origin_station_id FK
        VARCHAR destination_station_id FK
        JSONB stops_in_order 
        TIME first_train_time
        TIME last_train_time
        JSONB travel_time_from_origin_min 
        DECIMAL base_fare_usd "10,2"
        DECIMAL per_stop_rate_usd "10,2"
        INTEGER frequency_min
        JSONB operates_on 
    }

    %% 3. 反正規化設計 (攤平座位)
    national_rail_seats {
        VARCHAR schedule_id PK, FK
        VARCHAR seat_id PK
        VARCHAR coach 
        VARCHAR fare_class
        INTEGER seat_row
        VARCHAR seat_column 
    }

    %% 4. 交易與搭乘核心 (訂單與旅程)
    national_rail_bookings {
        VARCHAR booking_id PK
        VARCHAR user_id FK
        VARCHAR schedule_id FK
        VARCHAR origin_station_id FK
        VARCHAR destination_station_id FK
        DATE travel_date "NOT NULL"
        TIME departure_time "NOT NULL"
        VARCHAR ticket_type
        VARCHAR fare_class
        VARCHAR coach "NOT NULL"
        VARCHAR seat_id "NOT NULL"
        INTEGER stops_travelled "NOT NULL"
        DECIMAL amount_usd "10,2 NOT NULL"
        VARCHAR status "completed / confirmed / cancelled"
        TIMESTAMPTZ booked_at "NOT NULL"
        TIMESTAMPTZ travelled_at 
    }

    metro_trips {
        VARCHAR trip_id PK
        VARCHAR user_id FK
        VARCHAR schedule_id FK
        VARCHAR origin_station_id FK
        VARCHAR destination_station_id FK
        DATE travel_date "NOT NULL"
        VARCHAR ticket_type
        VARCHAR day_pass_ref FK "自遞迴"
        INTEGER stops_travelled 
        DECIMAL amount_usd "10,2 NOT NULL"
        VARCHAR status "completed / cancelled"
        TIMESTAMPTZ purchased_at 
        TIMESTAMPTZ travelled_at
    }

    %% 5. 【全新設計】各自獨立的支付與回饋表
    national_rail_payments {
        VARCHAR payment_id PK
        VARCHAR booking_id FK "嚴格參照 national_rail_bookings"
        DECIMAL amount_usd "10,2 NOT NULL"
        VARCHAR method
        VARCHAR status
        TIMESTAMPTZ paid_at "NOT NULL"
    }

    metro_payments {
        VARCHAR payment_id PK
        VARCHAR trip_id FK "嚴格參照 metro_trips"
        DECIMAL amount_usd "10,2 NOT NULL"
        VARCHAR method
        VARCHAR status
        TIMESTAMPTZ paid_at "NOT NULL"
    }

    national_rail_feedback {
        VARCHAR feedback_id PK
        VARCHAR booking_id FK "嚴格參照 national_rail_bookings"
        VARCHAR user_id FK "ON DELETE RESTRICT"
        INTEGER rating "CHECK (1-5)"
        TEXT comment 
        TIMESTAMPTZ submitted_at "NOT NULL"
    }

    metro_feedback {
        VARCHAR feedback_id PK
        VARCHAR trip_id FK "嚴格參照 metro_trips"
        VARCHAR user_id FK "ON DELETE RESTRICT"
        INTEGER rating "CHECK (1-5)"
        TEXT comment 
        TIMESTAMPTZ submitted_at "NOT NULL"
    }

    %% ==========================================
    %% 關係映射 (Relationships)
    %% ==========================================
    %% 基礎關聯
    national_rail_schedules ||--o{ national_rail_seats : "flattens"
    national_rail_schedules ||--o{ national_rail_bookings : "uses"
    metro_schedules ||--o{ metro_trips : "uses"
    
    national_rail_stations ||--o{ national_rail_bookings : "origin_of"
    national_rail_stations ||--o{ national_rail_bookings : "dest_of"
    metro_stations ||--o{ metro_trips : "origin_of"
    metro_stations ||--o{ metro_trips : "dest_of"
    metro_trips ||--o| metro_trips : "day_pass_ref"

    %% 使用者關聯
    registered_users ||--o{ national_rail_bookings : "places"
    registered_users ||--o{ metro_trips : "takes"
    registered_users ||--o{ national_rail_feedback : "submits"
    registered_users ||--o{ metro_feedback : "submits"

    %% 【拆分後的實體外鍵關聯 (實線)】
    national_rail_bookings ||--o{ national_rail_payments : "has_payment"
    metro_trips ||--o{ metro_payments : "has_payment"
    national_rail_bookings ||--o{ national_rail_feedback : "has_feedback"
    metro_trips ||--o{ metro_feedback : "has_feedback"
