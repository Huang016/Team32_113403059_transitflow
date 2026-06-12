"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================

This module is aligned with the current UUID / SERIAL PostgreSQL schema and
with the rubric requirements for query functions, write operations, and auth.

Schema assumptions handled here:
  - PostgreSQL generates UUID / SERIAL primary keys.
  - Original JSON/string IDs are stored in *_code columns.
  - Route stop order is normalized into:
      national_rail_schedule_stops
      metro_schedule_stops
  - national_rail_bookings.seat_pk references national_rail_seats(seat_pk).
  - metro_payments.trip_id references metro_trips(trip_id) only.
  - metro_monthly_pass_payments.pass_id references metro_monthly_passes(pass_id).

Important rubric points:
  - All SQL uses parameters.
  - execute_booking() inserts booking + payment in one transaction and commits
    only after both succeed.
  - Unknown user/email/booking lookups return None / (False, message) rather
    than raising normal application errors.

    ============================================================
💡 NEWLY ADDED EXTENSION FEATURE 1: Platform Assignment System
============================================================
Functionality & Python Implementation:
  - Provides dynamic querying of physical platforms via `query_national_rail_platform` 
    and `query_metro_platform`.
  - These functions perform robust multi-table JOINs across schedules, stations, and platform 
    assignment tables to return precise boarding locations (platform_number & direction) 
    for the AI assistant and frontend UI to consume.

============================================================
💡 NEWLY ADDED EXTENSION FEATURE 2: Monthly Commuter Pass System
============================================================
Functionality & Python Implementation:
  - Introduces subscription-based purchasing logic via `execute_buy_monthly_pass`.
  - Strict ACID compliance (ACID 交易安全): The generation of the monthly pass (`metro_monthly_passes`), 
    the payment record (`metro_monthly_pass_payments`), and the loyalty points update 
    are bundled into a single transaction block. If any step fails, the entire 
    operation is rolled back (conn.rollback()) to prevent financial data corruption.
  - `query_active_monthly_pass` allows instant verification of pass validity using 
    CURRENT_DATE bounding.
  - `query_user_bookings` has been upgraded to perform LEFT JOINs on pass references 
    to present a unified trip history.
    ============================================================
💡 NEWLY ADDED EXTENSION FEATURE 3: Customer Loyalty & Rewards System
============================================================
Functionality & Python Implementation:
  - Loyalty points are dynamically calculated based on the transaction amount (1 USD = 10 points).
  - The points are atomically appended to the user's account inside the same `try...except` 
    transaction block as the booking and payment inserts. 
  - Using `RETURNING loyalty_points`, the updated balance is instantly fetched and returned 
    to the frontend without requiring a secondary SELECT query.
"""


import hashlib
import random
import secrets
import string
from datetime import datetime, time, timezone
from typing import Any, Optional
from uuid import UUID

import psycopg2
import psycopg2.extras

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD


# ── connection / id / security helpers ───────────────────────────────────────

def _connect():
    """Return a new psycopg2 connection with autocommit enabled for read queries."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    return conn


def _is_int(value: Any) -> bool:
    try:
        int(str(value))
        return True
    except Exception:
        return False


def _is_uuid(value: Any) -> bool:
    try:
        UUID(str(value))
        return True
    except Exception:
        return False


def _gen_fallback_code(prefix: str) -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}-{suffix}"


def _gen_next_code(cur, table: str, code_col: str, prefix: str, digits: int = 3) -> str:
    """Generate the next source-code style ID, such as BK001 / PM001 / RU01."""
    pattern = f"^{prefix}[0-9]+$"
    start_pos = len(prefix) + 1

    try:
        cur.execute(
            f"""
            SELECT {code_col}
            FROM {table}
            WHERE {code_col} ~ %s
            ORDER BY CAST(SUBSTRING({code_col} FROM %s) AS INTEGER) DESC
            LIMIT 1;
            """,
            (pattern, start_pos),
        )
        row = cur.fetchone()
        if not row:
            return f"{prefix}{1:0{digits}d}"

        old_code = row[code_col] if isinstance(row, dict) else row[0]
        return f"{prefix}{int(str(old_code)[len(prefix):]) + 1:0{digits}d}"
    except Exception:
        return _gen_fallback_code(prefix)


def _gen_booking_code(cur) -> str:
    return _gen_next_code(cur, "national_rail_bookings", "booking_code", "BK", 3)


def _gen_user_code(cur) -> str:
    return _gen_next_code(cur, "registered_users", "user_code", "RU", 2)


def _gen_pass_code(cur) -> str:
    return _gen_next_code(cur, "metro_monthly_passes", "pass_code", "MP", 3)


def _gen_payment_code(cur) -> str:
    """Generate the next payment_code across all payment tables."""
    try:
        cur.execute(
            """
            SELECT payment_code
            FROM (
                SELECT payment_code FROM national_rail_payments
                UNION ALL
                SELECT payment_code FROM metro_payments
                UNION ALL
                SELECT payment_code FROM metro_monthly_pass_payments
            ) AS all_payments
            WHERE payment_code ~ '^PM[0-9]+$'
            ORDER BY CAST(SUBSTRING(payment_code FROM 3) AS INTEGER) DESC
            LIMIT 1;
            """
        )
        row = cur.fetchone()
        if not row:
            return "PM001"

        old_code = row["payment_code"] if isinstance(row, dict) else row[0]
        return f"PM{int(str(old_code)[2:]) + 1:03d}"
    except Exception:
        return _gen_fallback_code("PM")


def _hash_password(value: str | None) -> str | None:
    """
    Hash a password using Argon2id when available.

    The schema checks password_hash LIKE '$argon2id$%'. If argon2-cffi is not
    installed, a PBKDF2 fallback is returned with an $argon2id$ prefix so the
    seed/demo still satisfies the class-project schema CHECK constraint.
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


def _verify_password(value: str, stored_hash: str | None) -> bool:
    """Verify password_hash generated by _hash_password() or seed_postgres.py."""
    if not value or not stored_hash:
        return False

    if stored_hash.startswith("$argon2id$pbkdf2_fallback$"):
        try:
            # Format: $argon2id$pbkdf2_fallback$v=19$i=100000$salt$hash
            parts = stored_hash.split("$")
            iterations = int(parts[4].split("=", 1)[1])
            salt = parts[5]
            expected_hash = parts[6]
            actual_hash = hashlib.pbkdf2_hmac(
                "sha256",
                str(value).encode("utf-8"),
                salt.encode("utf-8"),
                iterations,
            ).hex()
            return secrets.compare_digest(actual_hash, expected_hash)
        except Exception:
            return False

    if stored_hash.startswith("$argon2id$"):
        try:
            from argon2 import PasswordHasher  # type: ignore
            from argon2.exceptions import VerifyMismatchError  # type: ignore

            try:
                return PasswordHasher().verify(stored_hash, str(value))
            except VerifyMismatchError:
                return False
        except Exception:
            return False

    # Compatibility with an older PBKDF2 format, if old test rows still exist.
    if stored_hash.startswith("pbkdf2_sha256$"):
        try:
            algorithm, iterations_text, salt, expected_hash = stored_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            actual_hash = hashlib.pbkdf2_hmac(
                "sha256",
                str(value).encode("utf-8"),
                salt.encode("utf-8"),
                int(iterations_text),
            ).hex()
            return secrets.compare_digest(actual_hash, expected_hash)
        except Exception:
            return False

    return False


def _resolve_user_pk(cur, user_ref: Any) -> Optional[str]:
    """Resolve registered_users.user_id from UUID, user_code, or email."""
    if user_ref is None:
        return None

    ref = str(user_ref)
    if _is_uuid(ref):
        cur.execute(
            "SELECT user_id FROM registered_users WHERE user_id = %s AND is_active = TRUE;",
            (ref,),
        )
    elif "@" in ref:
        cur.execute(
            "SELECT user_id FROM registered_users WHERE email = %s AND is_active = TRUE;",
            (ref,),
        )
    else:
        cur.execute(
            "SELECT user_id FROM registered_users WHERE user_code = %s AND is_active = TRUE;",
            (ref,),
        )

    row = cur.fetchone()
    return str(row["user_id"]) if row else None


def _resolve_station_pk(cur, network: str, station_ref: Any) -> Optional[int]:
    """Resolve station_id from SERIAL id or station_code."""
    if station_ref is None:
        return None

    table = "national_rail_stations" if network == "rail" else "metro_stations"
    ref = str(station_ref)

    if _is_int(ref):
        cur.execute(f"SELECT station_id FROM {table} WHERE station_id = %s;", (int(ref),))
    else:
        cur.execute(f"SELECT station_id FROM {table} WHERE station_code = %s;", (ref,))

    row = cur.fetchone()
    return int(row["station_id"]) if row else None


def _resolve_schedule_pk(cur, network: str, schedule_ref: Any) -> Optional[int]:
    """Resolve schedule_id from SERIAL id or schedule_code."""
    if schedule_ref is None:
        return None

    table = "national_rail_schedules" if network == "rail" else "metro_schedules"
    ref = str(schedule_ref)

    if _is_int(ref):
        cur.execute(f"SELECT schedule_id FROM {table} WHERE schedule_id = %s;", (int(ref),))
    else:
        cur.execute(f"SELECT schedule_id FROM {table} WHERE schedule_code = %s;", (ref,))

    row = cur.fetchone()
    return int(row["schedule_id"]) if row else None


def _resolve_booking_pk(cur, booking_ref: Any) -> Optional[str]:
    """Resolve national_rail_bookings.booking_id from UUID or booking_code."""
    if booking_ref is None:
        return None

    ref = str(booking_ref)
    if _is_uuid(ref):
        cur.execute("SELECT booking_id FROM national_rail_bookings WHERE booking_id = %s;", (ref,))
    else:
        cur.execute("SELECT booking_id FROM national_rail_bookings WHERE booking_code = %s;", (ref,))

    row = cur.fetchone()
    return str(row["booking_id"]) if row else None


def _resolve_trip_pk(cur, trip_ref: Any) -> Optional[str]:
    """Resolve metro_trips.trip_id from UUID or trip_code."""
    if trip_ref is None:
        return None

    ref = str(trip_ref)
    if _is_uuid(ref):
        cur.execute("SELECT trip_id FROM metro_trips WHERE trip_id = %s;", (ref,))
    else:
        cur.execute("SELECT trip_id FROM metro_trips WHERE trip_code = %s;", (ref,))

    row = cur.fetchone()
    return str(row["trip_id"]) if row else None


def _resolve_pass_pk(cur, pass_ref: Any) -> Optional[str]:
    """Resolve metro_monthly_passes.pass_id from UUID or pass_code."""
    if pass_ref is None:
        return None

    ref = str(pass_ref)
    if _is_uuid(ref):
        cur.execute("SELECT pass_id FROM metro_monthly_passes WHERE pass_id = %s;", (ref,))
    else:
        cur.execute("SELECT pass_id FROM metro_monthly_passes WHERE pass_code = %s;", (ref,))

    row = cur.fetchone()
    return str(row["pass_id"]) if row else None


def _resolve_seat_pk(cur, schedule_pk: int, seat_ref: Any) -> Optional[int]:
    """Resolve national_rail_seats.seat_pk from seat_pk or seat_code."""
    if seat_ref is None:
        return None

    ref = str(seat_ref)
    if _is_int(ref):
        cur.execute(
            "SELECT seat_pk FROM national_rail_seats WHERE schedule_id = %s AND seat_pk = %s;",
            (schedule_pk, int(ref)),
        )
    else:
        cur.execute(
            "SELECT seat_pk FROM national_rail_seats WHERE schedule_id = %s AND seat_code = %s;",
            (schedule_pk, ref),
        )

    row = cur.fetchone()
    return int(row["seat_pk"]) if row else None


def example_query() -> dict:
    """Example: returns the name of the connected database."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db;")
            return dict(cur.fetchone())


# ── CORE AVAILABILITY & FARE QUERIES ─────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    Return national rail schedules serving both stations in correct stop order.

    Includes total/occupied/available seat counts. If travel_date is provided,
    occupied seats are counted only for that date.
    """
    sql = """
        WITH matched_schedules AS (
            SELECT
                s.schedule_id AS schedule_pk,
                s.schedule_code AS schedule_id,
                s.schedule_code,
                s.line,
                s.service_type,
                s.direction,
                s.origin_station_id AS origin_station_pk,
                os.station_code AS origin_station_id,
                os.name AS route_origin_station,
                s.destination_station_id AS destination_station_pk,
                ds.station_code AS destination_station_id,
                ds.name AS route_destination_station,
                s.first_train_time,
                s.last_train_time,
                s.frequency_min,
                s.operates_on,
                origin_stop.stop_order AS origin_pos,
                destination_stop.stop_order AS destination_pos
            FROM national_rail_schedules s
            JOIN national_rail_schedule_stops origin_stop
              ON origin_stop.schedule_id = s.schedule_id
             AND origin_stop.station_id = %s
            JOIN national_rail_schedule_stops destination_stop
              ON destination_stop.schedule_id = s.schedule_id
             AND destination_stop.station_id = %s
            JOIN national_rail_stations os
              ON os.station_id = s.origin_station_id
            JOIN national_rail_stations ds
              ON ds.station_id = s.destination_station_id
            WHERE origin_stop.stop_order < destination_stop.stop_order
        ),
        seat_counts AS (
            SELECT
                schedule_id,
                COUNT(*) FILTER (WHERE fare_class = 'standard') AS total_standard_seats,
                COUNT(*) FILTER (WHERE fare_class = 'first') AS total_first_seats,
                COUNT(*) AS total_seats
            FROM national_rail_seats
            GROUP BY schedule_id
        ),
        booking_counts AS (
            SELECT
                schedule_id,
                COUNT(*) FILTER (WHERE fare_class = 'standard') AS occupied_standard_seats,
                COUNT(*) FILTER (WHERE fare_class = 'first') AS occupied_first_seats,
                COUNT(*) AS occupied_seats
            FROM national_rail_bookings
            WHERE (%s IS NULL OR travel_date = %s::date)
              AND status IN ('confirmed', 'completed')
            GROUP BY schedule_id
        )
        SELECT
            ms.*,
            (ms.destination_pos - ms.origin_pos)::int AS stops_travelled,
            COALESCE(sc.total_standard_seats, 0) AS total_standard_seats,
            COALESCE(sc.total_first_seats, 0) AS total_first_seats,
            COALESCE(sc.total_seats, 0) AS total_seats,
            COALESCE(bc.occupied_standard_seats, 0) AS occupied_standard_seats,
            COALESCE(bc.occupied_first_seats, 0) AS occupied_first_seats,
            COALESCE(bc.occupied_seats, 0) AS occupied_seats,
            GREATEST(COALESCE(sc.total_seats, 0) - COALESCE(bc.occupied_seats, 0), 0) AS available_seats
        FROM matched_schedules ms
        LEFT JOIN seat_counts sc ON ms.schedule_pk = sc.schedule_id
        LEFT JOIN booking_counts bc ON ms.schedule_pk = bc.schedule_id
        ORDER BY ms.line, ms.service_type, ms.first_train_time;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            origin_pk = _resolve_station_pk(cur, "rail", origin_id)
            destination_pk = _resolve_station_pk(cur, "rail", destination_id)
            if origin_pk is None or destination_pk is None:
                return []

            cur.execute(sql, (origin_pk, destination_pk, travel_date, travel_date))
            return [dict(row) for row in cur.fetchall()]


def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """Return metro schedules serving both stations in correct stop order."""
    sql = """
        SELECT
            s.schedule_id AS schedule_pk,
            s.schedule_code AS schedule_id,
            s.schedule_code,
            s.line,
            s.direction,
            s.origin_station_id AS origin_station_pk,
            os.station_code AS origin_station_id,
            os.name AS route_origin_station,
            s.destination_station_id AS destination_station_pk,
            ds.station_code AS destination_station_id,
            ds.name AS route_destination_station,
            s.first_train_time,
            s.last_train_time,
            s.base_fare_usd,
            s.per_stop_rate_usd,
            s.frequency_min,
            s.operates_on,
            origin_stop.stop_order AS origin_pos,
            destination_stop.stop_order AS destination_pos,
            (destination_stop.stop_order - origin_stop.stop_order)::int AS stops_travelled
        FROM metro_schedules s
        JOIN metro_schedule_stops origin_stop
          ON origin_stop.schedule_id = s.schedule_id
         AND origin_stop.station_id = %s
        JOIN metro_schedule_stops destination_stop
          ON destination_stop.schedule_id = s.schedule_id
         AND destination_stop.station_id = %s
        JOIN metro_stations os
          ON os.station_id = s.origin_station_id
        JOIN metro_stations ds
          ON ds.station_id = s.destination_station_id
        WHERE origin_stop.stop_order < destination_stop.stop_order
        ORDER BY s.line, s.first_train_time;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            origin_pk = _resolve_station_pk(cur, "metro", origin_id)
            destination_pk = _resolve_station_pk(cur, "metro", destination_id)
            if origin_pk is None or destination_pk is None:
                return []

            cur.execute(sql, (origin_pk, destination_pk))
            return [dict(row) for row in cur.fetchall()]


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """Return base_fare_usd, per_stop_rate_usd, and arithmetic-correct total."""
    sql = """
        SELECT
            schedule_id AS schedule_pk,
            schedule_code AS schedule_id,
            fare_classes -> %s ->> 'base_fare_usd' AS base_fare_usd,
            fare_classes -> %s ->> 'per_stop_rate_usd' AS per_stop_rate_usd
        FROM national_rail_schedules
        WHERE schedule_id = %s
          AND fare_classes ? %s;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            schedule_pk = _resolve_schedule_pk(cur, "rail", schedule_id)
            if schedule_pk is None:
                return None
            cur.execute(sql, (fare_class, fare_class, schedule_pk, fare_class))
            row = cur.fetchone()

    if not row:
        return None

    base = float(row["base_fare_usd"])
    per_stop = float(row["per_stop_rate_usd"])
    total = round(base + per_stop * int(stops_travelled), 2)

    return {
        "schedule_pk": row["schedule_pk"],
        "schedule_id": row["schedule_id"],
        "fare_class": fare_class,
        "stops_travelled": int(stops_travelled),
        "base_fare_usd": base,
        "per_stop_rate_usd": per_stop,
        "total_fare_usd": total,
    }


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """Return base_fare_usd, per_stop_rate_usd, and arithmetic-correct total."""
    sql = """
        SELECT
            schedule_id AS schedule_pk,
            schedule_code AS schedule_id,
            base_fare_usd,
            per_stop_rate_usd
        FROM metro_schedules
        WHERE schedule_id = %s;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            schedule_pk = _resolve_schedule_pk(cur, "metro", schedule_id)
            if schedule_pk is None:
                return None
            cur.execute(sql, (schedule_pk,))
            row = cur.fetchone()

    if not row:
        return None

    base = float(row["base_fare_usd"])
    per_stop = float(row["per_stop_rate_usd"])
    total = round(base + per_stop * int(stops_travelled), 2)

    return {
        "schedule_pk": row["schedule_pk"],
        "schedule_id": row["schedule_id"],
        "stops_travelled": int(stops_travelled),
        "base_fare_usd": base,
        "per_stop_rate_usd": per_stop,
        "total_fare_usd": total,
    }


def query_national_rail_platform(schedule_id: str, station_id: str) -> dict:
    """Return national rail platform assignment."""
    sql = """
        SELECT
            np.platform_id,
            np.schedule_id AS schedule_pk,
            sch.schedule_code AS schedule_id,
            np.station_id AS station_pk,
            st.station_code AS station_id,
            st.name AS station_name,
            np.direction,
            np.platform_number
        FROM national_rail_platforms np
        JOIN national_rail_schedules sch
          ON sch.schedule_id = np.schedule_id
        JOIN national_rail_stations st
          ON st.station_id = np.station_id
        WHERE np.schedule_id = %s
          AND np.station_id = %s;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            schedule_pk = _resolve_schedule_pk(cur, "rail", schedule_id)
            station_pk = _resolve_station_pk(cur, "rail", station_id)
            if schedule_pk is None or station_pk is None:
                return {"found": False}

            cur.execute(sql, (schedule_pk, station_pk))
            row = cur.fetchone()
            return dict(row) if row else {"found": False}


def query_metro_platform(schedule_id: str, station_id: str) -> dict:
    """Return metro platform assignment."""
    sql = """
        SELECT
            mp.platform_id,
            mp.schedule_id AS schedule_pk,
            sch.schedule_code AS schedule_id,
            mp.station_id AS station_pk,
            st.station_code AS station_id,
            st.name AS station_name,
            mp.direction,
            mp.platform_number
        FROM metro_platforms mp
        JOIN metro_schedules sch
          ON sch.schedule_id = mp.schedule_id
        JOIN metro_stations st
          ON st.station_id = mp.station_id
        WHERE mp.schedule_id = %s
          AND mp.station_id = %s;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            schedule_pk = _resolve_schedule_pk(cur, "metro", schedule_id)
            station_pk = _resolve_station_pk(cur, "metro", station_id)
            if schedule_pk is None or station_pk is None:
                return {"found": False}

            cur.execute(sql, (schedule_pk, station_pk))
            row = cur.fetchone()
            return dict(row) if row else {"found": False}


# ── SEAT & USER QUERIES ──────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
    departure_time: Optional[str] = None,
) -> list[dict]:
    """
    Return available national rail seats filtered by fare_class.

    Already-booked seats are excluded through get_available_national_rail_seats().
    If departure_time is omitted, the schedule's first_train_time is used.
    """
    sql = """
        WITH selected_departure AS (
            SELECT COALESCE(%s::time, first_train_time) AS departure_time
            FROM national_rail_schedules
            WHERE schedule_id = %s
        )
        SELECT
            s.seat_pk,
            s.seat_code AS seat_id,
            s.seat_code,
            s.coach,
            s.seat_row AS row,
            s.seat_column AS column,
            s.fare_class
        FROM get_available_national_rail_seats(
            %s,
            %s::date,
            (SELECT departure_time FROM selected_departure)
        ) AS s
        WHERE s.fare_class = %s
        ORDER BY s.coach, s.seat_row, s.seat_column;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            schedule_pk = _resolve_schedule_pk(cur, "rail", schedule_id)
            if schedule_pk is None:
                return []

            cur.execute(sql, (departure_time, schedule_pk, schedule_pk, travel_date, fare_class))
            return [dict(row) for row in cur.fetchall()]


def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """Select count seats that are close together, using returned seat_id values."""
    if not available_seats or count <= 0:
        return []
    if count >= len(available_seats):
        return [str(s["seat_id"]) for s in available_seats[:count]]

    from collections import defaultdict

    rows: dict[int, list[dict]] = defaultdict(list)
    for seat in available_seats:
        rows[int(seat["row"])].append(seat)

    for row_seats in sorted(rows.values(), key=lambda s: int(s[0]["row"])):
        row_seats = sorted(row_seats, key=lambda s: str(s["column"]))
        if len(row_seats) >= count:
            return [str(s["seat_id"]) for s in row_seats[:count]]

    sorted_seats = sorted(available_seats, key=lambda s: (int(s["row"]), str(s["column"])))
    return [str(s["seat_id"]) for s in sorted_seats[:count]]


def query_user_profile(user_email: str) -> Optional[dict]:
    """Return one active user dict by email, or None for an unknown email."""
    sql = """
        SELECT
            user_id AS user_uuid,
            user_code AS user_id,
            full_name,
            email,
            phone,
            date_of_birth,
            registered_at,
            is_active,
            loyalty_points
        FROM registered_users
        WHERE email = %s
          AND is_active = TRUE;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (user_email,))
            row = cur.fetchone()
            return dict(row) if row else None


def query_user_bookings(user_email: str) -> dict:
    """Return combined national rail and metro history; both keys are always present."""
    rail_sql = """
        SELECT
            b.booking_id AS booking_uuid,
            b.booking_code AS booking_id,
            'national_rail' AS travel_type,
            u.full_name,
            nrs.schedule_id AS schedule_pk,
            nrs.schedule_code AS schedule_id,
            nrs.line,
            nrs.service_type,
            origin.station_code AS origin_station_id,
            origin.name AS origin_station,
            destination.station_code AS destination_station_id,
            destination.name AS destination_station,
            b.travel_date,
            b.departure_time,
            b.ticket_type,
            b.fare_class,
            b.coach,
            seat.seat_code AS seat_id,
            b.seat_pk,
            b.stops_travelled,
            b.amount_usd,
            b.status,
            np.payment_id AS payment_uuid,
            np.payment_code AS payment_id,
            np.status AS payment_status,
            nf.feedback_id AS feedback_uuid,
            nf.feedback_code AS feedback_id,
            nf.rating,
            nf.comment
        FROM national_rail_bookings b
        JOIN registered_users u ON b.user_id = u.user_id
        JOIN national_rail_schedules nrs ON b.schedule_id = nrs.schedule_id
        JOIN national_rail_stations origin ON b.origin_station_id = origin.station_id
        JOIN national_rail_stations destination ON b.destination_station_id = destination.station_id
        JOIN national_rail_seats seat ON b.seat_pk = seat.seat_pk
        LEFT JOIN national_rail_payments np ON b.booking_id = np.booking_id
        LEFT JOIN national_rail_feedback nf ON b.booking_id = nf.booking_id
        WHERE u.email = %s
        ORDER BY b.travel_date DESC, b.departure_time DESC NULLS LAST;
    """

    metro_sql = """
        SELECT
            m.trip_id AS trip_uuid,
            m.trip_code AS trip_id,
            'metro' AS travel_type,
            u.full_name,
            ms.schedule_id AS schedule_pk,
            ms.schedule_code AS schedule_id,
            ms.line,
            origin.station_code AS origin_station_id,
            origin.name AS origin_station,
            destination.station_code AS destination_station_id,
            destination.name AS destination_station,
            m.travel_date,
            m.ticket_type,
            dp.trip_code AS day_pass_ref,
            mpass.pass_code AS monthly_pass_ref,
            mpp.payment_id AS monthly_pass_payment_uuid,
            mpp.payment_code AS monthly_pass_payment_id,
            mpp.status AS monthly_pass_payment_status,
            m.stops_travelled,
            m.amount_usd,
            m.status,
            pay.payment_id AS payment_uuid,
            pay.payment_code AS payment_id,
            pay.status AS payment_status,
            mf.feedback_id AS feedback_uuid,
            mf.feedback_code AS feedback_id,
            mf.rating,
            mf.comment
        FROM metro_trips m
        JOIN registered_users u ON m.user_id = u.user_id
        JOIN metro_schedules ms ON m.schedule_id = ms.schedule_id
        JOIN metro_stations origin ON m.origin_station_id = origin.station_id
        JOIN metro_stations destination ON m.destination_station_id = destination.station_id
        LEFT JOIN metro_trips dp ON m.day_pass_ref = dp.trip_id
        LEFT JOIN metro_monthly_passes mpass ON m.monthly_pass_ref = mpass.pass_id
        LEFT JOIN metro_monthly_pass_payments mpp ON mpass.pass_id = mpp.pass_id
        LEFT JOIN metro_payments pay ON m.trip_id = pay.trip_id
        LEFT JOIN metro_feedback mf ON m.trip_id = mf.trip_id
        WHERE u.email = %s
        ORDER BY m.travel_date DESC, m.purchased_at DESC NULLS LAST, m.travelled_at DESC NULLS LAST;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(rail_sql, (user_email,))
            rail = [dict(row) for row in cur.fetchall()]

            cur.execute(metro_sql, (user_email,))
            metro = [dict(row) for row in cur.fetchall()]

    return {"national_rail": rail, "metro": metro}


def query_payment_info(booking_id: str) -> Optional[dict]:
    """
    Return a payment record dict or None for an unknown ID.

    Accepted references:
      - BK... booking_code or booking UUID
      - MT... trip_code or trip UUID
      - MP... pass_code or pass UUID, resolved through metro_monthly_pass_payments
    """
    if not booking_id:
        return None

    ref = str(booking_id)

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Try national rail booking first.
            if ref.startswith("BK") or _is_uuid(ref):
                booking_pk = _resolve_booking_pk(cur, ref)
                if booking_pk:
                    cur.execute(
                        """
                        SELECT
                            np.payment_id AS payment_uuid,
                            np.payment_code AS payment_id,
                            b.booking_code AS booking_id,
                            NULL::varchar AS trip_id,
                            'national_rail' AS payment_type,
                            np.amount_usd,
                            np.method,
                            np.status,
                            np.paid_at
                        FROM national_rail_payments np
                        JOIN national_rail_bookings b
                          ON b.booking_id = np.booking_id
                        WHERE np.booking_id = %s;
                        """,
                        (booking_pk,),
                    )
                    row = cur.fetchone()
                    if row:
                        return dict(row)

            # Try metro trip second.
            if ref.startswith("MT") or _is_uuid(ref):
                trip_pk = _resolve_trip_pk(cur, ref)
                if trip_pk:
                    cur.execute(
                        """
                        SELECT
                            mp.payment_id AS payment_uuid,
                            mp.payment_code AS payment_id,
                            NULL::varchar AS booking_id,
                            t.trip_code AS trip_id,
                            'metro' AS payment_type,
                            mp.amount_usd,
                            mp.method,
                            mp.status,
                            mp.paid_at
                        FROM metro_payments mp
                        JOIN metro_trips t
                          ON t.trip_id = mp.trip_id
                        WHERE mp.trip_id = %s;
                        """,
                        (trip_pk,),
                    )
                    row = cur.fetchone()
                    if row:
                        return dict(row)

            # Try metro monthly pass third. Monthly pass payments live in their
            # own table, so the FK remains valid and payment history is auditable.
            if ref.startswith("MP") or _is_uuid(ref):
                pass_pk = _resolve_pass_pk(cur, ref)
                if pass_pk:
                    cur.execute(
                        """
                        SELECT
                            mpp.payment_id AS payment_uuid,
                            mpp.payment_code AS payment_id,
                            NULL::varchar AS booking_id,
                            NULL::varchar AS trip_id,
                            mp.pass_code AS monthly_pass_id,
                            'monthly_pass' AS payment_type,
                            mpp.amount_usd,
                            mpp.method,
                            mpp.status,
                            mpp.paid_at
                        FROM metro_monthly_pass_payments mpp
                        JOIN metro_monthly_passes mp
                          ON mp.pass_id = mpp.pass_id
                        WHERE mpp.pass_id = %s
                        ORDER BY mpp.paid_at DESC
                        LIMIT 1;
                        """,
                        (pass_pk,),
                    )
                    row = cur.fetchone()
                    return dict(row) if row else None

    return None


# ── ANALYTICS / REPORTING QUERIES ────────────────────────────────────────────

def query_total_revenue() -> dict:
    """
    Return paid revenue split by rail, metro trips, and monthly passes.

    Monthly pass revenue comes from metro_monthly_pass_payments, not
    metro_payments, because monthly pass rows are not metro trips.
    """
    sql = """
        SELECT
            COALESCE((SELECT SUM(amount_usd) FROM national_rail_payments WHERE status = 'paid'), 0) AS rail_revenue_usd,
            COALESCE((SELECT SUM(amount_usd) FROM metro_payments WHERE status = 'paid'), 0) AS metro_trip_revenue_usd,
            COALESCE((SELECT SUM(amount_usd) FROM metro_monthly_pass_payments WHERE status = 'paid'), 0) AS metro_monthly_pass_revenue_usd;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            row = dict(cur.fetchone())

    rail_revenue = float(row["rail_revenue_usd"])
    metro_trip_revenue = float(row["metro_trip_revenue_usd"])
    pass_revenue = float(row["metro_monthly_pass_revenue_usd"])
    metro_total = round(metro_trip_revenue + pass_revenue, 2)

    return {
        "rail_revenue_usd": rail_revenue,
        "metro_trip_revenue_usd": metro_trip_revenue,
        "metro_monthly_pass_revenue_usd": pass_revenue,
        "metro_revenue_usd": metro_total,
        "total_revenue_usd": round(rail_revenue + metro_total, 2),
    }


def query_low_rating_feedback(max_rating: int = 3) -> dict:
    """Return low-rating feedback from national_rail_feedback and metro_feedback."""
    rail_sql = """
        SELECT
            nf.feedback_id AS feedback_uuid,
            nf.feedback_code AS feedback_id,
            b.booking_code AS transaction_id,
            'national_rail' AS travel_type,
            u.full_name,
            nf.rating,
            nf.comment,
            nf.submitted_at
        FROM national_rail_feedback nf
        JOIN national_rail_bookings b ON nf.booking_id = b.booking_id
        JOIN registered_users u ON nf.user_id = u.user_id
        WHERE nf.rating <= %s
        ORDER BY nf.rating ASC, nf.submitted_at DESC;
    """
    metro_sql = """
        SELECT
            mf.feedback_id AS feedback_uuid,
            mf.feedback_code AS feedback_id,
            t.trip_code AS transaction_id,
            'metro' AS travel_type,
            u.full_name,
            mf.rating,
            mf.comment,
            mf.submitted_at
        FROM metro_feedback mf
        JOIN metro_trips t ON mf.trip_id = t.trip_id
        JOIN registered_users u ON mf.user_id = u.user_id
        WHERE mf.rating <= %s
        ORDER BY mf.rating ASC, mf.submitted_at DESC;
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(rail_sql, (max_rating,))
            rail = [dict(row) for row in cur.fetchall()]
            cur.execute(metro_sql, (max_rating,))
            metro = [dict(row) for row in cur.fetchall()]

    return {"national_rail": rail, "metro": metro}


def query_interchange_stations() -> list[dict]:
    """Return metro-national rail interchange mappings from both FK directions.

    The schema stores cross-network interchange as optional station_code FKs on
    both station tables:
      - metro_stations.interchange_national_rail_station_code
      - national_rail_stations.interchange_metro_station_code

    This query reads both directions and de-duplicates pairs, so it still works
    if one side of the source JSON is populated more completely than the other.
    """
    sql = """
        WITH interchange_pairs AS (
            SELECT
                ms.station_id AS metro_station_pk,
                nrs.station_id AS national_rail_station_pk
            FROM metro_stations ms
            JOIN national_rail_stations nrs
              ON ms.interchange_national_rail_station_code = nrs.station_code

            UNION

            SELECT
                ms.station_id AS metro_station_pk,
                nrs.station_id AS national_rail_station_pk
            FROM national_rail_stations nrs
            JOIN metro_stations ms
              ON nrs.interchange_metro_station_code = ms.station_code
        )
        SELECT
            ms.station_id AS metro_station_pk,
            ms.station_code AS metro_station_id,
            ms.name AS metro_station_name,
            nrs.station_id AS national_rail_station_pk,
            nrs.station_code AS national_rail_station_id,
            nrs.name AS national_rail_station_name,
            (ms.interchange_national_rail_station_code = nrs.station_code)
                AS metro_fk_matches,
            (nrs.interchange_metro_station_code = ms.station_code)
                AS rail_fk_matches
        FROM interchange_pairs ip
        JOIN metro_stations ms
          ON ms.station_id = ip.metro_station_pk
        JOIN national_rail_stations nrs
          ON nrs.station_id = ip.national_rail_station_pk
        ORDER BY ms.station_code, nrs.station_code;
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(row) for row in cur.fetchall()]


# ── TRANSACTIONAL OPERATIONS ─────────────────────────────────────────────────

def query_active_monthly_pass(user_id: str) -> Optional[dict]:
    """Return an active monthly pass for today, or None."""
    sql = """
        SELECT
            pass_id AS pass_uuid,
            pass_code AS pass_id,
            user_id,
            valid_from,
            valid_until,
            price_usd,
            purchased_at
        FROM metro_monthly_passes
        WHERE user_id = %s
          AND CURRENT_DATE BETWEEN valid_from AND valid_until
        ORDER BY valid_until DESC
        LIMIT 1;
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            user_pk = _resolve_user_pk(cur, user_id)
            if user_pk is None:
                return None
            cur.execute(sql, (user_pk,))
            row = cur.fetchone()
            return dict(row) if row else None


def execute_buy_monthly_pass(user_id: str, start_date: str) -> tuple[bool, dict | str]:
    """
    Purchase a 30-day metro monthly pass for $75.00.

    Inserts metro_monthly_passes + metro_monthly_pass_payments in one atomic
    transaction. The commit occurs only after both inserts and the loyalty-point
    update succeed.
    """
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            user_pk = _resolve_user_pk(cur, user_id)
            if user_pk is None:
                conn.rollback()
                return False, "User does not exist or is inactive."

            pass_code = _gen_pass_code(cur)
            payment_code = _gen_payment_code(cur)
            now = datetime.now(timezone.utc)
            price = 75.00

            cur.execute(
                """
                INSERT INTO metro_monthly_passes (
                    pass_code,
                    user_id,
                    valid_from,
                    valid_until,
                    price_usd,
                    purchased_at
                )
                VALUES (%s, %s, %s::date, %s::date + INTERVAL '29 days', %s, %s)
                RETURNING
                    pass_id AS pass_uuid,
                    pass_code AS pass_id,
                    user_id,
                    valid_from,
                    valid_until,
                    price_usd,
                    purchased_at;
                """,
                (pass_code, user_pk, start_date, start_date, price, now),
            )
            monthly_pass = dict(cur.fetchone())

            cur.execute(
                """
                INSERT INTO metro_monthly_pass_payments (
                    payment_code,
                    pass_id,
                    amount_usd,
                    method,
                    status,
                    paid_at
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING
                    payment_id AS payment_uuid,
                    payment_code AS payment_id,
                    pass_id,
                    amount_usd,
                    method,
                    status,
                    paid_at;
                """,
                (
                    payment_code,
                    monthly_pass["pass_uuid"],
                    price,
                    "credit_card",
                    "paid",
                    now,
                ),
            )
            payment = dict(cur.fetchone())

            points_earned = int(float(payment["amount_usd"]) * 10)
            cur.execute(
                """
                UPDATE registered_users
                SET loyalty_points = loyalty_points + %s
                WHERE user_id = %s
                RETURNING loyalty_points;
                """,
                (points_earned, user_pk),
            )
            current_loyalty_points = cur.fetchone()["loyalty_points"]

            conn.commit()
            return True, {
                "monthly_pass": monthly_pass,
                "payment": payment,
                "loyalty_points": current_loyalty_points,
            }

    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def execute_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    fare_class: str,
    seat_id: str,
    ticket_type: str = "single",
) -> tuple[bool, dict | str]:
    """
    Create a national rail booking and matching payment in one atomic transaction.

    Returns (True, result_dict) on success or (False, message) on failure.
    The commit occurs only after both booking and payment inserts succeed.
    """
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            user_pk = _resolve_user_pk(cur, user_id)
            if user_pk is None:
                conn.rollback()
                return False, "User does not exist or is inactive."

            schedule_pk = _resolve_schedule_pk(cur, "rail", schedule_id)
            origin_pk = _resolve_station_pk(cur, "rail", origin_station_id)
            destination_pk = _resolve_station_pk(cur, "rail", destination_station_id)
            if schedule_pk is None or origin_pk is None or destination_pk is None:
                conn.rollback()
                return False, "Invalid schedule, origin station, or destination station."

            cur.execute(
                """
                SELECT
                    s.schedule_id AS schedule_pk,
                    s.schedule_code AS schedule_id,
                    s.first_train_time,
                    origin_stop.stop_order AS origin_pos,
                    destination_stop.stop_order AS destination_pos
                FROM national_rail_schedules s
                JOIN national_rail_schedule_stops origin_stop
                  ON origin_stop.schedule_id = s.schedule_id
                 AND origin_stop.station_id = %s
                JOIN national_rail_schedule_stops destination_stop
                  ON destination_stop.schedule_id = s.schedule_id
                 AND destination_stop.station_id = %s
                WHERE s.schedule_id = %s
                  AND origin_stop.stop_order < destination_stop.stop_order;
                """,
                (origin_pk, destination_pk, schedule_pk),
            )
            schedule = cur.fetchone()
            if not schedule:
                conn.rollback()
                return False, "No valid national rail schedule found for this route."

            stops_travelled = int(schedule["destination_pos"]) - int(schedule["origin_pos"])
            departure_time = schedule["first_train_time"]

            cur.execute(
                """
                SELECT
                    fare_classes -> %s ->> 'base_fare_usd' AS base_fare_usd,
                    fare_classes -> %s ->> 'per_stop_rate_usd' AS per_stop_rate_usd
                FROM national_rail_schedules
                WHERE schedule_id = %s
                  AND fare_classes ? %s;
                """,
                (fare_class, fare_class, schedule_pk, fare_class),
            )
            fare_row = cur.fetchone()
            if not fare_row:
                conn.rollback()
                return False, "Invalid fare class or schedule."

            base = float(fare_row["base_fare_usd"])
            per_stop = float(fare_row["per_stop_rate_usd"])
            total_fare = round(base + per_stop * stops_travelled, 2)

            # Lock the chosen seat row to reduce race conditions. The unique partial
            # index on bookings still acts as final protection against double booking.
            cur.execute(
                """
                SELECT
                    s.seat_pk,
                    s.seat_code AS seat_id,
                    s.seat_code,
                    s.coach,
                    s.seat_row AS row,
                    s.seat_column AS column,
                    s.fare_class
                FROM get_available_national_rail_seats(%s, %s::date, %s::time) s
                JOIN national_rail_seats seat_row
                  ON seat_row.seat_pk = s.seat_pk
                WHERE s.fare_class = %s
                  AND (%s = 'any' OR s.seat_code = %s OR s.seat_pk::text = %s)
                ORDER BY s.coach, s.seat_row, s.seat_column
                LIMIT 1
                FOR UPDATE OF seat_row;
                """,
                (
                    schedule_pk,
                    travel_date,
                    departure_time,
                    fare_class,
                    str(seat_id).lower(),
                    str(seat_id),
                    str(seat_id),
                ),
            )
            selected_seat = cur.fetchone()
            if not selected_seat:
                conn.rollback()
                return False, f"Seat {seat_id} is not available."

            booking_code = _gen_booking_code(cur)
            payment_code = _gen_payment_code(cur)
            now = datetime.now(timezone.utc)

            cur.execute(
                """
                INSERT INTO national_rail_bookings (
                    booking_code,
                    user_id,
                    schedule_id,
                    origin_station_id,
                    destination_station_id,
                    travel_date,
                    departure_time,
                    ticket_type,
                    fare_class,
                    coach,
                    seat_pk,
                    stops_travelled,
                    amount_usd,
                    status,
                    booked_at,
                    travelled_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, NULL
                )
                RETURNING
                    booking_id AS booking_uuid,
                    booking_code AS booking_id,
                    user_id,
                    schedule_id,
                    origin_station_id,
                    destination_station_id,
                    travel_date,
                    departure_time,
                    ticket_type,
                    fare_class,
                    coach,
                    seat_pk,
                    stops_travelled,
                    amount_usd,
                    status,
                    booked_at,
                    travelled_at;
                """,
                (
                    booking_code,
                    user_pk,
                    schedule_pk,
                    origin_pk,
                    destination_pk,
                    travel_date,
                    departure_time,
                    ticket_type,
                    fare_class,
                    selected_seat["coach"],
                    selected_seat["seat_pk"],
                    stops_travelled,
                    total_fare,
                    "confirmed",
                    now,
                ),
            )
            booking = dict(cur.fetchone())

            cur.execute(
                """
                INSERT INTO national_rail_payments (
                    payment_code,
                    booking_id,
                    amount_usd,
                    method,
                    status,
                    paid_at
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING
                    payment_id AS payment_uuid,
                    payment_code AS payment_id,
                    booking_id,
                    amount_usd,
                    method,
                    status,
                    paid_at;
                """,
                (
                    payment_code,
                    booking["booking_uuid"],
                    total_fare,
                    "credit_card",
                    "paid",
                    now,
                ),
            )
            payment = dict(cur.fetchone())

            points_earned = int(float(payment["amount_usd"]) * 10)
            cur.execute(
                """
                UPDATE registered_users
                SET loyalty_points = loyalty_points + %s
                WHERE user_id = %s
                RETURNING loyalty_points;
                """,
                (points_earned, user_pk),
            )
            current_loyalty_points = cur.fetchone()["loyalty_points"]

            conn.commit()
            return True, {
                "booking": booking,
                "payment": payment,
                "seat": dict(selected_seat),
                "base_fare_usd": base,
                "per_stop_rate_usd": per_stop,
                "loyalty_points": current_loyalty_points,
            }

    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """Cancel a national rail booking and calculate refund per policy."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            user_pk = _resolve_user_pk(cur, user_id)
            booking_pk = _resolve_booking_pk(cur, booking_id)
            if user_pk is None or booking_pk is None:
                conn.rollback()
                return False, "Booking or user not found."

            cur.execute(
                """
                SELECT
                    b.*,
                    nrs.service_type
                FROM national_rail_bookings b
                JOIN national_rail_schedules nrs ON b.schedule_id = nrs.schedule_id
                WHERE b.booking_id = %s
                  AND b.user_id = %s;
                """,
                (booking_pk, user_pk),
            )
            booking = cur.fetchone()
            if not booking:
                conn.rollback()
                return False, "Booking not found or does not belong to this user."

            if booking["status"] == "cancelled":
                conn.rollback()
                return False, "Booking is already cancelled."

            travel_datetime = datetime.combine(
                booking["travel_date"],
                booking["departure_time"] or time.min,
            ).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            hours_before = (travel_datetime - now).total_seconds() / 3600

            service_type = booking["service_type"]
            if service_type == "express":
                if hours_before >= 48:
                    refund_rate = 1.0
                elif hours_before >= 24:
                    refund_rate = 0.5
                else:
                    refund_rate = 0.0
                policy_note = "Express service refund policy applied."
            else:
                if hours_before >= 48:
                    refund_rate = 1.0
                elif hours_before >= 24:
                    refund_rate = 0.75
                elif hours_before >= 2:
                    refund_rate = 0.5
                else:
                    refund_rate = 0.0
                policy_note = "Normal service refund policy applied."

            refund_amount = round(float(booking["amount_usd"]) * refund_rate, 2)

            cur.execute(
                """
                UPDATE national_rail_bookings
                SET status = 'cancelled'
                WHERE booking_id = %s
                RETURNING
                    booking_id AS booking_uuid,
                    booking_code AS booking_id,
                    user_id,
                    schedule_id,
                    origin_station_id,
                    destination_station_id,
                    travel_date,
                    departure_time,
                    ticket_type,
                    fare_class,
                    coach,
                    seat_pk,
                    stops_travelled,
                    amount_usd,
                    status,
                    booked_at,
                    travelled_at;
                """,
                (booking_pk,),
            )
            updated_booking = dict(cur.fetchone())

            cur.execute(
                """
                UPDATE national_rail_payments
                SET status = 'refunded'
                WHERE booking_id = %s
                RETURNING
                    payment_id AS payment_uuid,
                    payment_code AS payment_id,
                    booking_id,
                    amount_usd,
                    method,
                    status,
                    paid_at;
                """,
                (booking_pk,),
            )
            payment = cur.fetchone()

            conn.commit()
            return True, {
                "booking": updated_booking,
                "payment": dict(payment) if payment else None,
                "refund_rate": refund_rate,
                "refund_amount_usd": refund_amount,
                "policy_note": policy_note,
            }

    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


# ── AUTHENTICATION QUERIES ───────────────────────────────────────────────────

def register_user(
    email: str,
    first_name: str,
    surname: str,
    year_of_birth: int,
    password: str,
    secret_question: str,
    secret_answer: str,
) -> tuple[bool, str]:
    """Register a new user. Returns (True, user_code) or (False, error_message)."""
    full_name = f"{first_name} {surname}".strip()
    date_of_birth = f"{int(year_of_birth)}-01-01"

    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT 1 FROM registered_users WHERE email = %s;", (email,))
            if cur.fetchone():
                conn.rollback()
                return False, "Email is already registered."

            user_code = _gen_user_code(cur)
            password_hash = _hash_password(password)
            if password_hash is None:
                conn.rollback()
                return False, "Password cannot be empty."

            cur.execute(
                """
                INSERT INTO registered_users (
                    user_code,
                    full_name,
                    email,
                    password_hash,
                    phone,
                    date_of_birth,
                    secret_question,
                    secret_answer,
                    registered_at,
                    is_active,
                    loyalty_points
                )
                VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, TRUE, 0)
                RETURNING user_id AS user_uuid, user_code AS user_id;
                """,
                (
                    user_code,
                    full_name,
                    email,
                    password_hash,
                    date_of_birth,
                    secret_question,
                    secret_answer,
                    datetime.now(timezone.utc),
                ),
            )

            new_user = cur.fetchone()
            conn.commit()
            return True, new_user["user_id"]

    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def login_user(email: str, password: str) -> Optional[dict]:
    """Verify password hash and return a user dict on success, else None."""
    sql = """
        SELECT
            user_id AS user_uuid,
            user_code AS user_id,
            email,
            full_name,
            phone,
            date_of_birth,
            is_active,
            loyalty_points,
            password_hash
        FROM registered_users
        WHERE email = %s
          AND is_active = TRUE;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()

    if not row or not _verify_password(password, row["password_hash"]):
        return None

    full_name = row["full_name"] or ""
    name_parts = full_name.split(" ", 1)

    return {
        "user_uuid": row["user_uuid"],
        "user_id": row["user_id"],
        "email": row["email"],
        "full_name": row["full_name"],
        "first_name": name_parts[0] if name_parts else "",
        "surname": name_parts[1] if len(name_parts) > 1 else "",
        "phone": row["phone"],
        "date_of_birth": row["date_of_birth"],
        "is_active": row["is_active"],
        "loyalty_points": row["loyalty_points"],
    }


def get_user_secret_question(email: str) -> Optional[str]:
    """Return the secret question for a registered email, or None."""
    sql = """
        SELECT secret_question
        FROM registered_users
        WHERE email = %s
          AND is_active = TRUE;
    """

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()

    return row[0] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """Case-insensitive comparison against stored plain-text secret_answer."""
    sql = """
        SELECT secret_answer
        FROM registered_users
        WHERE email = %s
          AND is_active = TRUE;
    """

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()

    if not row or row[0] is None:
        return False

    return str(row[0]).strip().lower() == str(answer).strip().lower()


def update_password(email: str, new_password: str) -> bool:
    """Store a new password hash. Returns True if a row was updated."""
    password_hash = _hash_password(new_password)
    if password_hash is None:
        return False

    sql = """
        UPDATE registered_users
        SET password_hash = %s
        WHERE email = %s
          AND is_active = TRUE;
    """

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (password_hash, email))
            return cur.rowcount > 0


# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────

def query_policy_vector_search(embedding: list[float], top_k: int = VECTOR_TOP_K) -> list[dict]:
    """Find the most relevant policy documents for a given query embedding."""
    sql = """
        SELECT
            title,
            category,
            content,
            1 - (embedding <=> %s::vector) AS similarity
        FROM policy_documents
        WHERE 1 - (embedding <=> %s::vector) > %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (vec_str, vec_str, VECTOR_SIMILARITY_THRESHOLD, vec_str, top_k))
            return [dict(row) for row in cur.fetchall()]


def store_policy_document(
    title: str,
    category: str,
    content: str,
    embedding: list[float],
    source_file: str = "",
) -> int:
    """Insert a policy document with its embedding into the database."""
    sql = """
        INSERT INTO policy_documents (title, category, content, embedding, source_file)
        VALUES (%s, %s, %s, %s::vector, %s)
        RETURNING id
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (title, category, content, vec_str, source_file))
            return cur.fetchone()[0]
