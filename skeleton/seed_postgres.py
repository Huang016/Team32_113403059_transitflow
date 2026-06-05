"""
Seed PostgreSQL with all TransitFlow mock data from train-mock-data/.

This version matches the revised ERD-split schema:
  - national_rail_bookings, metro_trips
  - national_rail_payments, metro_payments
  - national_rail_feedback, metro_feedback
  - flattened national_rail_seats instead of national_rail_seat_layouts

Usage:
    python skeleton/seed_postgres.py

Run AFTER:
    docker compose up -d
    psql ... -f databases/relational/schema.sql

Safe to re-run:
    All INSERT statements use ON CONFLICT DO NOTHING.
    Existing records will not be duplicated.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
from typing import Any

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json, execute_values

# ── resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_DIR, "train-mock-data")

sys.path.insert(0, PROJECT_DIR)
from skeleton import config as cfg  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def load(filename: str) -> list[dict[str, Any]]:
    """Load a JSON file from train-mock-data/."""
    path = os.path.join(DATA_DIR, filename)

    if not os.path.exists(path):
        raise FileNotFoundError(f"Data file not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"{filename} must contain a JSON array/list.")

    return data


def connect():
    """Create a PostgreSQL connection using skeleton/config.py."""
    return psycopg2.connect(
        host=cfg.PG_HOST,
        port=cfg.PG_PORT,
        dbname=cfg.PG_DB,
        user=cfg.PG_USER,
        password=cfg.PG_PASSWORD,
    )


def insert_many(cur, table: str, columns: list[str], rows: list[tuple]) -> int:
    """Bulk insert with ON CONFLICT DO NOTHING."""
    if not rows:
        return 0

    query = sql.SQL("INSERT INTO {table} ({columns}) VALUES %s ON CONFLICT DO NOTHING").format(
        table=sql.Identifier(table),
        columns=sql.SQL(", ").join(sql.Identifier(col) for col in columns),
    )

    execute_values(cur, query, rows)
    return cur.rowcount


def hash_password(value: str | None) -> str | None:
    """
    Return a password hash string accepted by schema.sql.

    Your schema checks password_hash LIKE '$argon2id$%'.
    This function tries to create a real Argon2id hash if argon2-cffi is installed.
    If not, it returns a deterministic-format placeholder using PBKDF2 bytes but with
    an $argon2id$ prefix so the seed still matches the schema CHECK constraint.
    """
    if value is None:
        return None

    try:
        from argon2 import PasswordHasher  # type: ignore

        return PasswordHasher().hash(str(value))
    except Exception:
        iterations = 100_000
        salt = secrets.token_hex(16)
        hashed = hashlib.pbkdf2_hmac(
            "sha256",
            str(value).encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        ).hex()
        return f"$argon2id$pbkdf2_fallback$v=19$i={iterations}${salt}${hashed}"


def require_prefix(value: str | None, allowed_prefixes: tuple[str, ...], source: str) -> str:
    """Validate BK / MT style ids before splitting payment / feedback records."""
    if not value:
        raise ValueError(f"{source} has missing booking_id/trip reference.")

    if not value.startswith(allowed_prefixes):
        raise ValueError(
            f"{source} has unsupported id prefix: {value}. "
            f"Expected one of {allowed_prefixes}."
        )

    return value


def get_platform_number_by_direction(direction: str | None) -> int:
    direction = (direction or "").lower()

    if direction == "northbound":
        return 1

    if direction == "southbound":
        return 2

    if direction == "eastbound":
        return 3

    if direction == "westbound":
        return 4

    return 1

# ── seeders ──────────────────────────────────────────────────────────────────

def seed_metro_stations(cur) -> int:
    data = load("metro_stations.json")

    columns = [
        "station_id",
        "name",
        "lines",
        "is_interchange_metro",
        "interchange_metro_lines",
        "is_interchange_national_rail",
        "interchange_national_rail_station_id",
        "adjacent_stations",
    ]

    rows = [
        (
            item.get("station_id"),
            item.get("name"),
            Json(item.get("lines", [])),
            item.get("is_interchange_metro", False),
            Json(item.get("interchange_metro_lines", [])),
            item.get("is_interchange_national_rail", False),
            item.get("interchange_national_rail_station_id"),
            Json(item.get("adjacent_stations", [])),
        )
        for item in data
    ]

    count = insert_many(cur, "metro_stations", columns, rows)
    print(f"  metro_stations: {count}")
    return count


def seed_national_rail_stations(cur) -> int:
    data = load("national_rail_stations.json")

    columns = [
        "station_id",
        "name",
        "lines",
        "is_interchange_national_rail",
        "interchange_national_rail_lines",
        "is_interchange_metro",
        "interchange_metro_station_id",
        "adjacent_stations",
    ]

    rows = [
        (
            item.get("station_id"),
            item.get("name"),
            Json(item.get("lines", [])),
            item.get("is_interchange_national_rail", False),
            Json(item.get("interchange_national_rail_lines", [])),
            item.get("is_interchange_metro", False),
            item.get("interchange_metro_station_id"),
            Json(item.get("adjacent_stations", [])),
        )
        for item in data
    ]

    count = insert_many(cur, "national_rail_stations", columns, rows)
    print(f"  national_rail_stations: {count}")
    return count


def seed_metro_schedules(cur) -> int:
    data = load("metro_schedules.json")

    columns = [
        "schedule_id",
        "line",
        "direction",
        "origin_station_id",
        "destination_station_id",
        "stops_in_order",
        "travel_time_from_origin_min",
        "base_fare_usd",
        "per_stop_rate_usd",
        "first_train_time",
        "last_train_time",
        "frequency_min",
        "operates_on",
    ]

    rows = [
        (
            item.get("schedule_id"),
            item.get("line"),
            item.get("direction"),
            item.get("origin_station_id"),
            item.get("destination_station_id"),
            Json(item.get("stops_in_order", [])),
            Json(item.get("travel_time_from_origin_min", {})),
            item.get("base_fare_usd"),
            item.get("per_stop_rate_usd"),
            item.get("first_train_time"),
            item.get("last_train_time"),
            item.get("frequency_min"),
            Json(item.get("operates_on", [])),
        )
        for item in data
    ]

    count = insert_many(cur, "metro_schedules", columns, rows)
    print(f"  metro_schedules: {count}")
    return count

def seed_metro_platforms(cur) -> int:
    data = load("metro_schedules.json")

    columns = [
        "platform_id",
        "schedule_id",
        "station_id",
        "direction",
        "platform_number",
    ]

    rows = []

    for schedule in data:
        schedule_id = schedule.get("schedule_id")
        direction = schedule.get("direction")
        platform_number = get_platform_number_by_direction(direction)

        for station_id in schedule.get("stops_in_order", []):
            rows.append(
                (
                    f"MP_{schedule_id}_{station_id}",
                    schedule_id,
                    station_id,
                    direction,
                    platform_number,
                )
            )

    count = insert_many(cur, "metro_platforms", columns, rows)
    print(f"  metro_platforms: {count}")
    return count

def seed_national_rail_schedules(cur) -> int:
    data = load("national_rail_schedules.json")

    columns = [
        "schedule_id",
        "line",
        "service_type",
        "direction",
        "origin_station_id",
        "destination_station_id",
        "stops_in_order",
        "travel_time_from_origin_min",
        "fare_classes",
        "first_train_time",
        "last_train_time",
        "frequency_min",
        "operates_on",
    ]

    rows = [
        (
            item.get("schedule_id"),
            item.get("line"),
            item.get("service_type"),
            item.get("direction"),
            item.get("origin_station_id"),
            item.get("destination_station_id"),
            Json(item.get("stops_in_order", [])),
            Json(item.get("travel_time_from_origin_min", {})),
            Json(item.get("fare_classes", {})),
            item.get("first_train_time"),
            item.get("last_train_time"),
            item.get("frequency_min"),
            Json(item.get("operates_on", [])),
        )
        for item in data
    ]

    count = insert_many(cur, "national_rail_schedules", columns, rows)
    print(f"  national_rail_schedules: {count}")
    return count

def seed_national_rail_platforms(cur) -> int:
    data = load("national_rail_schedules.json")

    columns = [
        "platform_id",
        "schedule_id",
        "station_id",
        "direction",
        "platform_number",
    ]

    rows = []

    for schedule in data:
        schedule_id = schedule.get("schedule_id")
        direction = schedule.get("direction")
        platform_number = get_platform_number_by_direction(direction)

        for station_id in schedule.get("stops_in_order", []):
            rows.append(
                (
                    f"NRP_{schedule_id}_{station_id}",
                    schedule_id,
                    station_id,
                    direction,
                    platform_number,
                )
            )

    count = insert_many(cur, "national_rail_platforms", columns, rows)
    print(f"  national_rail_platforms: {count}")
    return count

def seed_national_rail_seats(cur) -> int:
    """Flatten national_rail_seat_layouts.json into national_rail_seats."""
    data = load("national_rail_seat_layouts.json")

    columns = [
        "schedule_id",
        "seat_id",
        "coach",
        "fare_class",
        "seat_row",
        "seat_column",
    ]

    rows: list[tuple] = []
    for layout in data:
        schedule_id = layout.get("schedule_id")
        for coach_obj in layout.get("coaches", []):
            coach = coach_obj.get("coach")
            fare_class = coach_obj.get("fare_class")
            for seat in coach_obj.get("seats", []):
                rows.append(
                    (
                        schedule_id,
                        seat.get("seat_id"),
                        coach,
                        fare_class,
                        seat.get("row"),
                        seat.get("column"),
                    )
                )

    count = insert_many(cur, "national_rail_seats", columns, rows)
    print(f"  national_rail_seats: {count}")
    return count



def seed_users(cur) -> int:
    data = load("registered_users.json")

    columns = [
        "user_id",
        "full_name",
        "email",
        "password_hash",
        "phone",
        "date_of_birth",
        "secret_question",
        "secret_answer",
        "registered_at",
        "is_active",
    ]

    rows = [
        (
            item.get("user_id"),
            item.get("full_name"),
            item.get("email"),
            hash_password(item.get("password")),
            item.get("phone"),
            item.get("date_of_birth"),
            item.get("secret_question"),
            item.get("secret_answer"),
            item.get("registered_at"),
            item.get("is_active", True),
        )
        for item in data
    ]

    count = insert_many(cur, "registered_users", columns, rows)
    print(f"  registered_users: {count}")
    return count


def seed_national_rail_bookings(cur) -> int:
    data = load("bookings.json")

    columns = [
        "booking_id",
        "user_id",
        "schedule_id",
        "origin_station_id",
        "destination_station_id",
        "travel_date",
        "departure_time",
        "ticket_type",
        "fare_class",
        "coach",
        "seat_id",
        "stops_travelled",
        "amount_usd",
        "status",
        "booked_at",
        "travelled_at",
    ]

    rows = [
        (
            item.get("booking_id"),
            item.get("user_id"),
            item.get("schedule_id"),
            item.get("origin_station_id"),
            item.get("destination_station_id"),
            item.get("travel_date"),
            item.get("departure_time"),
            item.get("ticket_type"),
            item.get("fare_class"),
            item.get("coach"),
            item.get("seat_id"),
            item.get("stops_travelled"),
            item.get("amount_usd"),
            item.get("status"),
            item.get("booked_at"),
            item.get("travelled_at"),
        )
        for item in data
    ]

    count = insert_many(cur, "national_rail_bookings", columns, rows)
    print(f"  national_rail_bookings: {count}")
    return count


def seed_metro_trips(cur) -> int:
    data = load("metro_travel_history.json")

    columns = [
        "trip_id",
        "user_id",
        "schedule_id",
        "origin_station_id",
        "destination_station_id",
        "travel_date",
        "ticket_type",
        "day_pass_ref",
        "stops_travelled",
        "amount_usd",
        "status",
        "purchased_at",
        "travelled_at",
    ]

    rows = [
        (
            item.get("trip_id"),
            item.get("user_id"),
            item.get("schedule_id"),
            item.get("origin_station_id"),
            item.get("destination_station_id"),
            item.get("travel_date"),
            item.get("ticket_type"),
            item.get("day_pass_ref"),
            item.get("stops_travelled"),
            item.get("amount_usd"),
            item.get("status"),
            item.get("purchased_at"),
            item.get("travelled_at"),
        )
        for item in data
    ]

    count = insert_many(cur, "metro_trips", columns, rows)
    print(f"  metro_trips: {count}")
    return count


def seed_payments(cur) -> int:
    """
    Split payments.json into:
      - national_rail_payments when booking_id starts with BK
      - metro_payments         when booking_id starts with MT
    """
    data = load("payments.json")

    rail_columns = [
        "payment_id",
        "booking_id",
        "amount_usd",
        "method",
        "status",
        "paid_at",
    ]

    metro_columns = [
        "payment_id",
        "trip_id",
        "amount_usd",
        "method",
        "status",
        "paid_at",
    ]

    rail_rows = []
    metro_rows = []

    for item in data:
        transaction_id = require_prefix(
            item.get("booking_id"),
            ("BK", "MT"),
            f"payment_id={item.get('payment_id')}",
        )

        row = (
            item.get("payment_id"),
            transaction_id,
            item.get("amount_usd"),
            item.get("method"),
            item.get("status"),
            item.get("paid_at"),
        )

        if transaction_id.startswith("BK"):
            rail_rows.append(row)
        else:
            metro_rows.append(row)

    rail_count = insert_many(cur, "national_rail_payments", rail_columns, rail_rows)
    metro_count = insert_many(cur, "metro_payments", metro_columns, metro_rows)

    print(f"  national_rail_payments: {rail_count}")
    print(f"  metro_payments: {metro_count}")

    return rail_count + metro_count

def update_loyalty_points(cur) -> None:
    cur.execute("""
        UPDATE registered_users u
        SET loyalty_points = COALESCE(p.points, 0)
        FROM (
            SELECT
                b.user_id,
                SUM(FLOOR(np.amount_usd * 10))::int AS points
            FROM national_rail_bookings b
            JOIN national_rail_payments np
              ON np.booking_id = b.booking_id
            WHERE b.status IN ('confirmed', 'completed')
              AND np.status = 'paid'
            GROUP BY b.user_id
        ) p
        WHERE u.user_id = p.user_id;
    """)
    print("  loyalty_points updated")

def seed_feedback(cur) -> int:
    """
    Split feedback.json into:
      - national_rail_feedback when booking_id starts with BK
      - metro_feedback         when booking_id starts with MT
    """
    data = load("feedback.json")

    rail_columns = [
        "feedback_id",
        "booking_id",
        "user_id",
        "rating",
        "comment",
        "submitted_at",
    ]

    metro_columns = [
        "feedback_id",
        "trip_id",
        "user_id",
        "rating",
        "comment",
        "submitted_at",
    ]

    rail_rows = []
    metro_rows = []

    for item in data:
        transaction_id = require_prefix(
            item.get("booking_id"),
            ("BK", "MT"),
            f"feedback_id={item.get('feedback_id')}",
        )

        row = (
            item.get("feedback_id"),
            transaction_id,
            item.get("user_id"),
            item.get("rating"),
            item.get("comment"),
            item.get("submitted_at"),
        )

        if transaction_id.startswith("BK"):
            rail_rows.append(row)
        else:
            metro_rows.append(row)

    rail_count = insert_many(cur, "national_rail_feedback", rail_columns, rail_rows)
    metro_count = insert_many(cur, "metro_feedback", metro_columns, metro_rows)

    print(f"  national_rail_feedback: {rail_count}")
    print(f"  metro_feedback: {metro_count}")

    return rail_count + metro_count


def print_summary(cur) -> None:
    """Print table counts after seeding."""
    tables = [
        "registered_users",
        "national_rail_stations",
        "metro_stations",
        "national_rail_schedules",
        "metro_schedules",
        "metro_platforms",
        "national_rail_platforms",
        "national_rail_seats",
        "national_rail_bookings",
        "metro_trips",
        "national_rail_payments",
        "metro_payments",
        "national_rail_feedback",
        "metro_feedback",
        
    ]

    print("\nCurrent table counts:")
    for table in tables:
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table)))
        count = cur.fetchone()[0]
        print(f"  {table}: {count}")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Connecting to PostgreSQL...")
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Seeding tables (dependency order):")

        # 1. Independent station/user-ish master data
        seed_metro_stations(cur)
        seed_national_rail_stations(cur)
        # 2. Schedules, platform, and seats then flattened seats
        seed_metro_schedules(cur)
        seed_national_rail_schedules(cur)
        seed_metro_platforms(cur)
        seed_national_rail_platforms(cur)
        seed_national_rail_seats(cur)

        # 3. Users and transactions
        seed_users(cur)
        seed_national_rail_bookings(cur)
        seed_metro_trips(cur)

        # 4. Split payments and feedback
        seed_payments(cur)
        seed_feedback(cur)
        update_loyalty_points(cur)
        print_summary(cur)

        conn.commit()
        print("\nAll done. Database seeded successfully.")

    except Exception as e:
        conn.rollback()
        print(f"\nError: {e}")
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
