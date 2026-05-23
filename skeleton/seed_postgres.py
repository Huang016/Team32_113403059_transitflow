"""
Seed PostgreSQL with all TransitFlow mock data from train-mock-data/.

Usage:
    python skeleton/seed_postgres.py

Run AFTER docker compose up -d.
You must first design and create your tables in databases/relational/schema.sql.

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
from skeleton import config as cfg


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
    """
    Bulk insert with ON CONFLICT DO NOTHING.

    Uses psycopg2.sql.Identifier for safer table/column handling.
    Returns the cursor rowcount reported by psycopg2.
    """
    if not rows:
        return 0

    query = sql.SQL("INSERT INTO {table} ({columns}) VALUES %s ON CONFLICT DO NOTHING").format(
        table=sql.Identifier(table),
        columns=sql.SQL(", ").join(sql.Identifier(col) for col in columns),
    )

    execute_values(cur, query, rows)
    return cur.rowcount


def hash_value(value: str | None) -> str | None:
    """
    Hash password / secret_answer before saving to DB.

    Format:
        pbkdf2_sha256$iterations$salt$hash

    Note:
        The salt is random, so the hash value changes every time a fresh DB is seeded.
        This is normal and more secure than storing plain text.
    """
    if value is None:
        return None

    iterations = 100_000
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        str(value).encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()

    return f"pbkdf2_sha256${iterations}${salt}${hashed}"


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
    ]

    rows = [
        (
            item.get("station_id"),
            item.get("name"),
            item.get("lines"),
            item.get("is_interchange_metro"),
            item.get("interchange_metro_lines"),
            item.get("is_interchange_national_rail"),
            None,  # circular FK fixed later by update_station_interchange_fks()
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
    ]

    rows = [
        (
            item.get("station_id"),
            item.get("name"),
            item.get("lines"),
            item.get("is_interchange_national_rail"),
            item.get("interchange_national_rail_lines"),
            item.get("is_interchange_metro"),
            None,  # circular FK fixed later by update_station_interchange_fks()
        )
        for item in data
    ]

    count = insert_many(cur, "national_rail_stations", columns, rows)
    print(f"  national_rail_stations: {count}")
    return count


def update_station_interchange_fks(cur) -> None:
    """
    metro_stations and national_rail_stations reference each other.

    To avoid circular FK errors:
      1. Insert both station tables with NULL interchange ids.
      2. Update interchange ids after both tables already exist and contain data.
    """
    metro_data = load("metro_stations.json")
    national_rail_data = load("national_rail_stations.json")

    metro_updates = [
        (
            item.get("interchange_national_rail_station_id"),
            item.get("station_id"),
        )
        for item in metro_data
        if item.get("interchange_national_rail_station_id") is not None
    ]

    if metro_updates:
        execute_values(
            cur,
            """
            UPDATE metro_stations AS ms
            SET interchange_national_rail_station_id = data.interchange_national_rail_station_id
            FROM (VALUES %s) AS data(interchange_national_rail_station_id, station_id)
            WHERE ms.station_id = data.station_id
            """,
            metro_updates,
        )

    rail_updates = [
        (
            item.get("interchange_metro_station_id"),
            item.get("station_id"),
        )
        for item in national_rail_data
        if item.get("interchange_metro_station_id") is not None
    ]

    if rail_updates:
        execute_values(
            cur,
            """
            UPDATE national_rail_stations AS nrs
            SET interchange_metro_station_id = data.interchange_metro_station_id
            FROM (VALUES %s) AS data(interchange_metro_station_id, station_id)
            WHERE nrs.station_id = data.station_id
            """,
            rail_updates,
        )

    print("  station interchange FK updated")


def seed_metro_schedules(cur) -> int:
    data = load("metro_schedules.json")

    columns = [
        "schedule_id",
        "line",
        "direction",
        "origin_station_id",
        "destination_station_id",
        "stops_in_order",
        "first_train_time",
        "last_train_time",
        "travel_time_from_origin_min",
        "base_fare_usd",
        "per_stop_rate_usd",
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
            item.get("stops_in_order"),
            item.get("first_train_time"),
            item.get("last_train_time"),
            Json(item.get("travel_time_from_origin_min")),
            item.get("base_fare_usd"),
            item.get("per_stop_rate_usd"),
            item.get("frequency_min"),
            item.get("operates_on"),
        )
        for item in data
    ]

    count = insert_many(cur, "metro_schedules", columns, rows)
    print(f"  metro_schedules: {count}")
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
        "passed_through_stations",
        "first_train_time",
        "last_train_time",
        "travel_time_from_origin_min",
        "fare_classes",
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
            item.get("stops_in_order"),
            item.get("passed_through_stations"),
            item.get("first_train_time"),
            item.get("last_train_time"),
            Json(item.get("travel_time_from_origin_min")),
            Json(item.get("fare_classes")),
            item.get("frequency_min"),
            item.get("operates_on"),
        )
        for item in data
    ]

    count = insert_many(cur, "national_rail_schedules", columns, rows)
    print(f"  national_rail_schedules: {count}")
    return count


def seed_seat_layouts(cur) -> int:
    data = load("national_rail_seat_layouts.json")

    columns = [
        "layout_id",
        "schedule_id",
        "coaches",
    ]

    rows = [
        (
            item.get("layout_id"),
            item.get("schedule_id"),
            Json(item.get("coaches")),
        )
        for item in data
    ]

    count = insert_many(cur, "national_rail_seat_layouts", columns, rows)
    print(f"  national_rail_seat_layouts: {count}")
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
        "secret_answer_hash",
        "registered_at",
        "is_active",
    ]

    rows = [
        (
            item.get("user_id"),
            item.get("full_name"),
            item.get("email"),
            hash_value(item.get("password")),
            item.get("phone"),
            item.get("date_of_birth"),
            item.get("secret_question"),
            hash_value(item.get("secret_answer")),
            item.get("registered_at"),
            item.get("is_active"),
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

    count = insert_many(cur, "bookings", columns, rows)
    print(f"  bookings: {count}")
    return count


def seed_metro_travels(cur) -> int:
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

    count = insert_many(cur, "metro_travel_history", columns, rows)
    print(f"  metro_travel_history: {count}")
    return count


def seed_payments(cur) -> int:
    """
    Split original payments.json into:
      - rail_payments  when booking_id starts with BK
      - metro_payments when booking_id starts with MT
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

    rail_count = insert_many(cur, "rail_payments", rail_columns, rail_rows)
    metro_count = insert_many(cur, "metro_payments", metro_columns, metro_rows)

    print(f"  rail_payments: {rail_count}")
    print(f"  metro_payments: {metro_count}")

    return rail_count + metro_count


def seed_feedback(cur) -> int:
    """
    Split original feedback.json into:
      - rail_feedback  when booking_id starts with BK
      - metro_feedback when booking_id starts with MT
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

    rail_count = insert_many(cur, "rail_feedback", rail_columns, rail_rows)
    metro_count = insert_many(cur, "metro_feedback", metro_columns, metro_rows)

    print(f"  rail_feedback: {rail_count}")
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
        "national_rail_seat_layouts",
        "bookings",
        "metro_travel_history",
        "rail_payments",
        "metro_payments",
        "rail_feedback",
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

        # 1. Stations
        seed_metro_stations(cur)
        seed_national_rail_stations(cur)
        update_station_interchange_fks(cur)

        # 2. Schedules and layouts
        seed_metro_schedules(cur)
        seed_national_rail_schedules(cur)
        seed_seat_layouts(cur)

        # 3. Users and transactions
        seed_users(cur)
        seed_national_rail_bookings(cur)
        seed_metro_travels(cur)

        # 4. Payments and feedback
        seed_payments(cur)
        seed_feedback(cur)

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
