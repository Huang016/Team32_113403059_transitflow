# AI Session Context — TransitFlow

**How to use this file:**
At the start of every AI coding session, paste the full contents of this file as your first message to your AI assistant. This gives the AI the context it needs to produce code that fits your codebase and is consistent with your teammates' work.

**Who maintains this file:**
Whoever makes a schema change or architectural decision updates this file in the same commit. Treat it like a team contract.

---

## Project Overview

TransitFlow is a Python-based AI chat assistant for a fictional transit operator. It queries three databases — PostgreSQL (relational + vector), Neo4j (graph) — and uses an LLM to answer user questions. Our task as students is to design the database schema and implement the query functions in `databases/relational/queries.py` and `databases/graph/queries.py`.

## Tech Stack

- Language: Python 3.11+
- Relational DB: PostgreSQL via `psycopg2` with `RealDictCursor`
- Graph DB: Neo4j via the `neo4j` Python driver
- Vector search: `pgvector` extension (already implemented — do not modify)
- Web UI: Gradio
- LLM: Google Gemini or local Ollama (configured via `.env`)

## Coding Conventions

- **Naming:** `snake_case` for all Python names and SQL identifiers
- **Docstrings:** All functions must have a docstring with `Args:` and `Returns:` sections
- **Return types:** Use type hints. Read-only functions return `list[dict]` or `Optional[dict]`
- **Empty results:** Return `[]` or `None` (as documented), never raise an exception for "not found"
- **SQL:** Use `%s` placeholders for all user inputs — never string-format into SQL
- **Relational pattern:** Use `_connect()` helper + `psycopg2.extras.RealDictCursor`:
  ```python
  with _connect() as conn:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT ...", (param,))
          return [dict(row) for row in cur.fetchall()]
  ```
- **Graph pattern:** Use `_driver()` helper + session:
  ```python
  with _driver() as driver:
      with driver.session() as session:
          result = session.run("MATCH ...", station_id=station_id)
          return [dict(record) for record in result]
  ```

## Agreed Relational Schema

<!-- ============================================================
  FILL THIS IN after your team completes the schema design workshop.
  Paste your final CREATE TABLE statements here.
  ============================================================ -->

```sql
-- TODO: paste your final schema.sql contents here after team review
```## Agreed Relational Schema

```sql
-- 使用者帳號管理
CREATE TABLE registered_users (
    user_id VARCHAR PRIMARY KEY,
    full_name VARCHAR NOT NULL,
    email VARCHAR UNIQUE NOT NULL,
    password_hash VARCHAR,
    phone VARCHAR,
    date_of_birth DATE,
    secret_question VARCHAR,
    secret_answer_hash VARCHAR,
    registered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- 國家鐵路與捷運車站
CREATE TABLE national_rail_stations (
    station_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL
);

CREATE TABLE metro_stations (
    station_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    interchange_national_rail_station_id VARCHAR REFERENCES national_rail_stations(station_id)
);

-- 國家鐵路時刻表與座位配置
CREATE TABLE national_rail_schedules (
    schedule_id VARCHAR PRIMARY KEY,
    line VARCHAR,
    service_type VARCHAR,
    direction VARCHAR,
    origin_station_id VARCHAR REFERENCES national_rail_stations(station_id),
    destination_station_id VARCHAR REFERENCES national_rail_stations(station_id),
    stops_in_order VARCHAR[] NOT NULL,
    first_train_time TIME,
    last_train_time TIME,
    frequency_min INTEGER,
    operates_on VARCHAR,
    fare_classes JSONB -- 包含 base_fare_usd 與 per_stop_rate_usd
);

CREATE TABLE national_rail_seat_layouts (
    schedule_id VARCHAR PRIMARY KEY REFERENCES national_rail_schedules(schedule_id),
    coaches JSONB -- 包含 coach, fare_class, 以及 seats (seat_id, row, column)
);

-- 捷運時刻表
CREATE TABLE metro_schedules (
    schedule_id VARCHAR PRIMARY KEY,
    line VARCHAR,
    direction VARCHAR,
    origin_station_id VARCHAR REFERENCES metro_stations(station_id),
    destination_station_id VARCHAR REFERENCES metro_stations(station_id),
    stops_in_order VARCHAR[] NOT NULL,
    first_train_time TIME,
    last_train_time TIME,
    travel_time_from_origin_min INTEGER,
    base_fare_usd NUMERIC(10, 2),
    per_stop_rate_usd NUMERIC(10, 2),
    frequency_min INTEGER,
    operates_on VARCHAR
);

-- 國家鐵路訂票與紀錄
CREATE TABLE bookings (
    booking_id VARCHAR PRIMARY KEY,
    user_id VARCHAR REFERENCES registered_users(user_id),
    schedule_id VARCHAR REFERENCES national_rail_schedules(schedule_id),
    origin_station_id VARCHAR REFERENCES national_rail_stations(station_id),
    destination_station_id VARCHAR REFERENCES national_rail_stations(station_id),
    travel_date DATE,
    departure_time TIME,
    ticket_type VARCHAR,
    fare_class VARCHAR,
    coach VARCHAR,
    seat_id VARCHAR,
    stops_travelled INTEGER,
    amount_usd NUMERIC(10, 2),
    status VARCHAR,
    booked_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    travelled_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE rail_payments (
    payment_id VARCHAR PRIMARY KEY,
    booking_id VARCHAR REFERENCES bookings(booking_id),
    amount_usd NUMERIC(10, 2),
    method VARCHAR,
    status VARCHAR,
    paid_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE rail_feedback (
    feedback_id VARCHAR PRIMARY KEY,
    booking_id VARCHAR REFERENCES bookings(booking_id),
    user_id VARCHAR REFERENCES registered_users(user_id),
    rating INTEGER,
    comment TEXT,
    submitted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 捷運乘車紀錄
CREATE TABLE metro_travel_history (
    trip_id VARCHAR PRIMARY KEY,
    user_id VARCHAR REFERENCES registered_users(user_id),
    schedule_id VARCHAR REFERENCES metro_schedules(schedule_id),
    origin_station_id VARCHAR REFERENCES metro_stations(station_id),
    destination_station_id VARCHAR REFERENCES metro_stations(station_id),
    travel_date DATE,
    ticket_type VARCHAR,
    day_pass_ref VARCHAR,
    stops_travelled INTEGER,
    amount_usd NUMERIC(10, 2),
    status VARCHAR,
    purchased_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE metro_payments (
    payment_id VARCHAR PRIMARY KEY,
    trip_id VARCHAR REFERENCES metro_travel_history(trip_id),
    amount_usd NUMERIC(10, 2),
    method VARCHAR,
    status VARCHAR,
    paid_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE metro_feedback (
    feedback_id VARCHAR PRIMARY KEY,
    trip_id VARCHAR REFERENCES metro_travel_history(trip_id),
    user_id VARCHAR REFERENCES registered_users(user_id),
    rating INTEGER,
    comment TEXT,
    submitted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- RAG 向量搜尋文件 (依循 pgvector 設定)
CREATE TABLE policy_documents (
    id SERIAL PRIMARY KEY,
    title VARCHAR,
    category VARCHAR,
    content TEXT,
    embedding VECTOR(768), -- 假設使用標準的 1536 維度，請依實際模型調整
    source_file VARCHAR
);

## Agreed Graph Schema

<!-- ============================================================
  FILL THIS IN after your team agrees on Neo4j node labels and
  relationship types.
  ============================================================ -->
## Agreed Graph Schema

Node labels:
- `Station`: 統一的車站節點（包含捷運與國鐵）。
  - 可搭配屬性 `network` 來區分系統，確保 `query_delay_ripple` 能跨系統計算 N 跳（Hops）影響範圍。

Relationship types:
- `CONNECTS_TO`: 同一路線相鄰車站之間的運行連線。
- `INTERCHANGE_TO`: 捷運站與國鐵站之間的實體轉乘通道（如 Central Square `MS01` ⇄ Central Station `NR01`）。

Key properties:
- `Station` 屬性：
  - `station_id` (String, 主鍵唯一約束，例如 "MS01", "NR01")
  - `name` (String, 車站名稱)
  - `network` (String, 系統類別: "metro" 或 "rail")
  - `lines` (List of Strings, 該站停靠的路線清單，例如 ["M1", "M2"])
- `CONNECTS_TO` 屬性（用於 Dijkstra 權重計算）：
  - `line` (String, 路線名稱，如 "M1", "NR1")
  - `service_type` (String, 服務類型: "normal" 或 "express")
  - `travel_time_min` (Float/Integer, 站間行駛時間 ── 最快路徑權重)
  - `fare_standard` (Float, 標準艙/捷運每跨一站的費率 ── 最便宜路徑權重)
  - `fare_first` (Float, 頭等艙每跨一站的費率，國鐵專用)
- `INTERCHANGE_TO` 屬性：
  - `travel_time_min` (Float, 轉乘步行時間，預設為 0)
  - `fare` (Float, 轉乘本身費用，固定為 0.0)


## Function Signatures We Are Implementing

These are fixed contracts. AI-generated code must match these signatures exactly.

### Relational (`databases/relational/queries.py`)

```python
# Read-only
def query_national_rail_availability(origin_id: str, destination_id: str, travel_date: Optional[str] = None) -> list[dict]: ...
def query_national_rail_fare(schedule_id: str, fare_class: str, stops_travelled: int) -> Optional[dict]: ...
def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]: ...
def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]: ...
def query_available_seats(schedule_id: str, travel_date: str, fare_class: str) -> list[dict]: ...
def query_user_profile(user_email: str) -> Optional[dict]: ...
def query_user_bookings(user_email: str) -> dict: ...  # returns {"national_rail": [...], "metro": [...]}
def query_payment_info(booking_id: str) -> Optional[dict]: ...

# Write operations
def execute_booking(user_id, schedule_id, origin_station_id, destination_station_id, travel_date, fare_class, seat_id, ticket_type="single") -> tuple[bool, dict | str]: ...
def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]: ...

# Auth
def register_user(email, first_name, surname, year_of_birth, password, secret_question, secret_answer) -> tuple[bool, str]: ...
def login_user(email: str, password: str) -> Optional[dict]: ...
def get_user_secret_question(email: str) -> Optional[str]: ...
def verify_secret_answer(email: str, answer: str) -> bool: ...
def update_password(email: str, new_password: str) -> bool: ...
```

### Graph (`databases/graph/queries.py`)

```python
def query_shortest_route(origin_id: str, destination_id: str, network: str = "auto") -> dict: ...
def query_cheapest_route(origin_id: str, destination_id: str, network: str = "auto", fare_class: str = "standard") -> dict: ...
def query_alternative_routes(origin_id, destination_id, avoid_station_id, network="auto", max_routes=3) -> list[list[dict]]: ...
def query_interchange_path(origin_id: str, destination_id: str) -> dict: ...
def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]: ...
def query_station_connections(station_id: str) -> list[dict]: ...
```

## Team Decisions Log

<!-- Add entries as you make decisions. Format: "Decision: X. Why: Y." -->

- [ ] Schema design: TODO — add your table/column decisions here
- [ ] Graph schema: TODO — add your node label and relationship type decisions here
- [ ] (example) Metro schedule stop ordering: using `jsonb_array_elements` approach — easier to debug than containment operators

## Prompts That Worked

<!-- Share prompts that produced good output so teammates can reuse them. -->

### Schema design prompt that worked:
```
TODO — add a prompt here after your schema design workshop
```

### Query implementation prompt that worked:
```
TODO — add after implementing your first function
```
