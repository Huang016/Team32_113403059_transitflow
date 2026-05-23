"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================
This module handles all queries to PostgreSQL.

Payments and feedback follow the split made in seed_postgres.py:
  - BK... records -> rail_payments / rail_feedback
  - MT... records -> metro_payments / metro_feedback

Passwords and secret answers follow the same hash format used in seed_postgres.py:
  pbkdf2_sha256$iterations$salt$hash
"""

from __future__ import annotations

import hashlib
import random
import secrets
import string
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD


# ── connection / id / security helpers ───────────────────────────────────────

def _connect():
    """Return a new psycopg2 connection with autocommit enabled."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    return conn


def _gen_fallback_id(prefix: str) -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}-{suffix}"


def _gen_booking_id(cur) -> str:
    """
    Generate the next booking id in the same style as seed data: BK001, BK002...
    Falls back to BK-XXXXXX if parsing fails.
    """
    try:
        cur.execute(
            """
            SELECT booking_id
            FROM bookings
            WHERE booking_id ~ '^BK[0-9]+$'
            ORDER BY CAST(SUBSTRING(booking_id FROM 3) AS INTEGER) DESC
            LIMIT 1;
            """
        )
        row = cur.fetchone()
        if not row:
            return "BK001"
        return f"BK{int(row['booking_id'][2:]) + 1:03d}"
    except Exception:
        return _gen_fallback_id("BK")


def _gen_payment_id(cur) -> str:
    """
    Generate the next payment id in the same style as seed data: PM001, PM002...
    Checks both rail_payments and metro_payments because seed_postgres.py splits payments.
    """
    try:
        cur.execute(
            """
            SELECT payment_id
            FROM (
                SELECT payment_id FROM rail_payments
                UNION ALL
                SELECT payment_id FROM metro_payments
            ) AS all_payments
            WHERE payment_id ~ '^PM[0-9]+$'
            ORDER BY CAST(SUBSTRING(payment_id FROM 3) AS INTEGER) DESC
            LIMIT 1;
            """
        )
        row = cur.fetchone()
        if not row:
            return "PM001"
        return f"PM{int(row['payment_id'][2:]) + 1:03d}"
    except Exception:
        return _gen_fallback_id("PM")


def _gen_user_id(cur) -> str:
    """Generate the next user id in the same style as seed data: RU01, RU02..."""
    cur.execute(
        """
        SELECT user_id
        FROM registered_users
        WHERE user_id ~ '^RU[0-9]+$'
        ORDER BY CAST(SUBSTRING(user_id FROM 3) AS INTEGER) DESC
        LIMIT 1;
        """
    )
    row = cur.fetchone()
    if not row:
        return "RU01"
    return f"RU{int(row['user_id'][2:]) + 1:02d}"


def _hash_value(value: str | None) -> str | None:
    """
    Hash password / secret_answer.

    Must match seed_postgres.py:
        pbkdf2_sha256$iterations$salt$hash
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


def _verify_hash(value: str, stored_hash: str | None) -> bool:
    """
    Verify a value against pbkdf2_sha256$iterations$salt$hash.
    Returns False if the stored hash is missing or malformed.
    """
    if not value or not stored_hash:
        return False

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


def example_query() -> dict:
    """Example: returns the name of the connected database."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db;")
            return dict(cur.fetchone())


# ── NATIONAL RAIL AVAILABILITY ────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    Return national rail schedules that serve both origin and destination stations
    in the correct order, along with seat occupancy for the requested travel date.
    """
    sql = """
        WITH matched_schedules AS (
            SELECT
                schedule_id,
                line,
                service_type,
                direction,
                origin_station_id,
                destination_station_id,
                stops_in_order,
                first_train_time,
                last_train_time,
                frequency_min,
                operates_on,
                array_position(stops_in_order, %s) AS origin_pos,
                array_position(stops_in_order, %s) AS destination_pos
            FROM national_rail_schedules
            WHERE array_position(stops_in_order, %s) IS NOT NULL
              AND array_position(stops_in_order, %s) IS NOT NULL
              AND array_position(stops_in_order, %s) < array_position(stops_in_order, %s)
        ),
        seat_counts AS (
            SELECT
                l.schedule_id,
                COUNT(*) FILTER (WHERE coach ->> 'fare_class' = 'standard') AS total_standard_seats,
                COUNT(*) FILTER (WHERE coach ->> 'fare_class' = 'first') AS total_first_seats,
                COUNT(*) AS total_seats
            FROM national_rail_seat_layouts l
            CROSS JOIN LATERAL jsonb_array_elements(l.coaches) AS coach
            CROSS JOIN LATERAL jsonb_array_elements(coach -> 'seats') AS seat
            GROUP BY l.schedule_id
        ),
        booking_counts AS (
            SELECT
                schedule_id,
                COUNT(*) FILTER (WHERE fare_class = 'standard') AS occupied_standard_seats,
                COUNT(*) FILTER (WHERE fare_class = 'first') AS occupied_first_seats,
                COUNT(*) AS occupied_seats
            FROM bookings
            WHERE (%s IS NULL OR travel_date = %s::date)
              AND status <> 'cancelled'
            GROUP BY schedule_id
        )
        SELECT
            ms.*,
            (ms.destination_pos - ms.origin_pos) AS stops_travelled,
            COALESCE(sc.total_standard_seats, 0) AS total_standard_seats,
            COALESCE(sc.total_first_seats, 0) AS total_first_seats,
            COALESCE(sc.total_seats, 0) AS total_seats,
            COALESCE(bc.occupied_standard_seats, 0) AS occupied_standard_seats,
            COALESCE(bc.occupied_first_seats, 0) AS occupied_first_seats,
            COALESCE(bc.occupied_seats, 0) AS occupied_seats,
            COALESCE(sc.total_seats, 0) - COALESCE(bc.occupied_seats, 0) AS available_seats
        FROM matched_schedules ms
        LEFT JOIN seat_counts sc ON ms.schedule_id = sc.schedule_id
        LEFT JOIN booking_counts bc ON ms.schedule_id = bc.schedule_id
        ORDER BY ms.line, ms.service_type, ms.first_train_time;
    """

    params = (
        origin_id,
        destination_id,
        origin_id,
        destination_id,
        origin_id,
        destination_id,
        travel_date,
        travel_date,
    )

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """
    Calculate the fare for a national rail journey.
    Formula:
        total = base_fare_usd + per_stop_rate_usd * stops_travelled
    """
    sql = """
        SELECT
            schedule_id,
            fare_classes -> %s ->> 'base_fare_usd' AS base_fare_usd,
            fare_classes -> %s ->> 'per_stop_rate_usd' AS per_stop_rate_usd
        FROM national_rail_schedules
        WHERE schedule_id = %s
          AND fare_classes ? %s;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (fare_class, fare_class, schedule_id, fare_class))
            row = cur.fetchone()

    if not row:
        return None

    base = float(row["base_fare_usd"])
    per_stop = float(row["per_stop_rate_usd"])
    total = round(base + per_stop * int(stops_travelled), 2)

    return {
        "schedule_id": schedule_id,
        "fare_class": fare_class,
        "stops_travelled": stops_travelled,
        "base_fare_usd": base,
        "per_stop_rate_usd": per_stop,
        "total_fare_usd": total,
    }


# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """Return metro schedules that serve both origin and destination in the correct order."""
    sql = """
        SELECT
            schedule_id,
            line,
            direction,
            origin_station_id,
            destination_station_id,
            stops_in_order,
            first_train_time,
            last_train_time,
            travel_time_from_origin_min,
            base_fare_usd,
            per_stop_rate_usd,
            frequency_min,
            operates_on,
            array_position(stops_in_order, %s) AS origin_pos,
            array_position(stops_in_order, %s) AS destination_pos,
            array_position(stops_in_order, %s) - array_position(stops_in_order, %s) AS stops_travelled
        FROM metro_schedules
        WHERE array_position(stops_in_order, %s) IS NOT NULL
          AND array_position(stops_in_order, %s) IS NOT NULL
          AND array_position(stops_in_order, %s) < array_position(stops_in_order, %s)
        ORDER BY line, first_train_time;
    """

    params = (
        origin_id,
        destination_id,
        destination_id,
        origin_id,
        origin_id,
        destination_id,
        origin_id,
        destination_id,
    )

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """
    Calculate the metro fare.
    Formula:
        total = base_fare_usd + per_stop_rate_usd * stops_travelled
    """
    sql = """
        SELECT schedule_id, base_fare_usd, per_stop_rate_usd
        FROM metro_schedules
        WHERE schedule_id = %s;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()

    if not row:
        return None

    base = float(row["base_fare_usd"])
    per_stop = float(row["per_stop_rate_usd"])
    total = round(base + per_stop * int(stops_travelled), 2)

    return {
        "schedule_id": schedule_id,
        "stops_travelled": stops_travelled,
        "base_fare_usd": base,
        "per_stop_rate_usd": per_stop,
        "total_fare_usd": total,
    }


# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """Return available seats for a national rail journey on a given date."""
    sql = """
        WITH layout_seats AS (
            SELECT
                l.schedule_id,
                coach ->> 'coach' AS coach,
                coach ->> 'fare_class' AS fare_class,
                seat ->> 'seat_id' AS seat_id,
                (seat ->> 'row')::int AS row,
                seat ->> 'column' AS column
            FROM national_rail_seat_layouts l
            CROSS JOIN LATERAL jsonb_array_elements(l.coaches) AS coach
            CROSS JOIN LATERAL jsonb_array_elements(coach -> 'seats') AS seat
            WHERE l.schedule_id = %s
              AND coach ->> 'fare_class' = %s
        ),
        occupied AS (
            SELECT seat_id
            FROM bookings
            WHERE schedule_id = %s
              AND travel_date = %s::date
              AND fare_class = %s
              AND status <> 'cancelled'
              AND seat_id IS NOT NULL
        )
        SELECT
            ls.seat_id,
            ls.coach,
            ls.row,
            ls.column,
            ls.fare_class
        FROM layout_seats ls
        LEFT JOIN occupied o ON ls.seat_id = o.seat_id
        WHERE o.seat_id IS NULL
        ORDER BY ls.coach, ls.row, ls.column;
    """

    params = (schedule_id, fare_class, schedule_id, travel_date, fare_class)

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """
    Select `count` seats that are as close together as possible.
    Same row preferred, then adjacent rows.
    """
    if not available_seats or count <= 0:
        return []
    if count >= len(available_seats):
        return [s["seat_id"] for s in available_seats[:count]]

    from collections import defaultdict
    rows: dict[int, list[dict]] = defaultdict(list)
    for seat in available_seats:
        rows[seat["row"]].append(seat)

    for row_seats in sorted(rows.values(), key=lambda s: s[0]["row"]):
        if len(row_seats) >= count:
            return [s["seat_id"] for s in row_seats[:count]]

    sorted_seats = sorted(available_seats, key=lambda s: (s["row"], s["column"]))
    return [s["seat_id"] for s in sorted_seats[:count]]


# ── USER & BOOKING QUERIES ────────────────────────────────────────────────────

def query_user_profile(user_email: str) -> Optional[dict]:
    """Return a user's profile by email. Does not expose password_hash or secret_answer_hash."""
    sql = """
        SELECT
            user_id,
            full_name,
            email,
            phone,
            date_of_birth,
            registered_at,
            is_active
        FROM registered_users
        WHERE email = %s;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (user_email,))
            row = cur.fetchone()
            return dict(row) if row else None


def query_user_bookings(user_email: str) -> dict:
    """
    Return a user's combined booking history.

    Seed split logic:
      - National rail records are in bookings + rail_payments + rail_feedback.
      - Metro records are in metro_travel_history + metro_payments + metro_feedback.
    """
    rail_sql = """
        SELECT
            b.booking_id,
            'national_rail' AS travel_type,
            u.full_name,
            b.schedule_id,
            nrs.line,
            nrs.service_type,
            origin.name AS origin_station,
            destination.name AS destination_station,
            b.travel_date,
            b.departure_time,
            b.ticket_type,
            b.fare_class,
            b.coach,
            b.seat_id,
            b.stops_travelled,
            b.amount_usd,
            b.status,
            rp.payment_id,
            rp.status AS payment_status,
            rf.rating,
            rf.comment
        FROM bookings b
        JOIN registered_users u ON b.user_id = u.user_id
        JOIN national_rail_schedules nrs ON b.schedule_id = nrs.schedule_id
        JOIN national_rail_stations origin ON b.origin_station_id = origin.station_id
        JOIN national_rail_stations destination ON b.destination_station_id = destination.station_id
        LEFT JOIN rail_payments rp ON b.booking_id = rp.booking_id
        LEFT JOIN rail_feedback rf ON b.booking_id = rf.booking_id
        WHERE u.email = %s
        ORDER BY b.travel_date DESC, b.departure_time DESC NULLS LAST;
    """

    metro_sql = """
        SELECT
            m.trip_id,
            'metro' AS travel_type,
            u.full_name,
            m.schedule_id,
            ms.line,
            origin.name AS origin_station,
            destination.name AS destination_station,
            m.travel_date,
            m.ticket_type,
            m.day_pass_ref,
            m.stops_travelled,
            m.amount_usd,
            m.status,
            mp.payment_id,
            mp.status AS payment_status,
            mf.rating,
            mf.comment
        FROM metro_travel_history m
        JOIN registered_users u ON m.user_id = u.user_id
        JOIN metro_schedules ms ON m.schedule_id = ms.schedule_id
        JOIN metro_stations origin ON m.origin_station_id = origin.station_id
        JOIN metro_stations destination ON m.destination_station_id = destination.station_id
        LEFT JOIN metro_payments mp ON m.trip_id = mp.trip_id
        LEFT JOIN metro_feedback mf ON m.trip_id = mf.trip_id
        WHERE u.email = %s
        ORDER BY m.travel_date DESC, m.purchased_at DESC NULLS LAST;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(rail_sql, (user_email,))
            rail = [dict(row) for row in cur.fetchall()]

            cur.execute(metro_sql, (user_email,))
            metro = [dict(row) for row in cur.fetchall()]

    return {
        "national_rail": rail,
        "metro": metro,
    }


def query_payment_info(booking_id: str) -> Optional[dict]:
    """
    Return payment record for a booking or metro trip.

    This follows seed_postgres.py split logic:
      - BK... -> rail_payments.booking_id
      - MT... -> metro_payments.trip_id
    """
    if booking_id.startswith("BK"):
        sql = """
            SELECT
                payment_id,
                booking_id,
                NULL::varchar AS trip_id,
                'national_rail' AS payment_type,
                amount_usd,
                method,
                status,
                paid_at
            FROM rail_payments
            WHERE booking_id = %s;
        """
    elif booking_id.startswith("MT"):
        sql = """
            SELECT
                payment_id,
                NULL::varchar AS booking_id,
                trip_id,
                'metro' AS payment_type,
                amount_usd,
                method,
                status,
                paid_at
            FROM metro_payments
            WHERE trip_id = %s;
        """
    else:
        return None

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (booking_id,))
            row = cur.fetchone()
            return dict(row) if row else None


# ── ANALYTICS / REPORTING QUERIES ────────────────────────────────────────────

def query_total_revenue() -> dict:
    """Return paid revenue split by national rail and metro."""
    sql = """
        SELECT
            COALESCE((SELECT SUM(amount_usd) FROM rail_payments WHERE status = 'paid'), 0) AS rail_revenue_usd,
            COALESCE((SELECT SUM(amount_usd) FROM metro_payments WHERE status = 'paid'), 0) AS metro_revenue_usd;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            row = dict(cur.fetchone())

    rail_revenue = float(row["rail_revenue_usd"])
    metro_revenue = float(row["metro_revenue_usd"])

    return {
        "rail_revenue_usd": rail_revenue,
        "metro_revenue_usd": metro_revenue,
        "total_revenue_usd": round(rail_revenue + metro_revenue, 2),
    }


def query_low_rating_feedback(max_rating: int = 3) -> dict:
    """Return low rating feedback from both rail_feedback and metro_feedback."""
    rail_sql = """
        SELECT
            rf.feedback_id,
            rf.booking_id AS transaction_id,
            'national_rail' AS travel_type,
            u.full_name,
            rf.rating,
            rf.comment,
            rf.submitted_at
        FROM rail_feedback rf
        JOIN registered_users u ON rf.user_id = u.user_id
        WHERE rf.rating <= %s
        ORDER BY rf.rating ASC, rf.submitted_at DESC;
    """
    metro_sql = """
        SELECT
            mf.feedback_id,
            mf.trip_id AS transaction_id,
            'metro' AS travel_type,
            u.full_name,
            mf.rating,
            mf.comment,
            mf.submitted_at
        FROM metro_feedback mf
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
    """Return metro-national rail interchange mapping after seed FK update."""
    sql = """
        SELECT
            ms.station_id AS metro_station_id,
            ms.name AS metro_station_name,
            nrs.station_id AS national_rail_station_id,
            nrs.name AS national_rail_station_name
        FROM metro_stations ms
        JOIN national_rail_stations nrs
          ON ms.interchange_national_rail_station_id = nrs.station_id
        ORDER BY ms.station_id;
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(row) for row in cur.fetchall()]


# ── TRANSACTIONAL OPERATIONS ──────────────────────────────────────────────────

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
    """Create a national rail booking and matching rail_payments record."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id FROM registered_users WHERE user_id = %s AND is_active = TRUE;",
                (user_id,),
            )
            if not cur.fetchone():
                conn.rollback()
                return False, "User does not exist or is inactive."

            cur.execute(
                """
                SELECT
                    schedule_id,
                    stops_in_order,
                    first_train_time,
                    fare_classes
                FROM national_rail_schedules
                WHERE schedule_id = %s
                  AND array_position(stops_in_order, %s) IS NOT NULL
                  AND array_position(stops_in_order, %s) IS NOT NULL
                  AND array_position(stops_in_order, %s) < array_position(stops_in_order, %s);
                """,
                (
                    schedule_id,
                    origin_station_id,
                    destination_station_id,
                    origin_station_id,
                    destination_station_id,
                ),
            )
            schedule = cur.fetchone()
            if not schedule:
                conn.rollback()
                return False, "No valid national rail schedule found for this route."

            stops = list(schedule["stops_in_order"])
            stops_travelled = stops.index(destination_station_id) - stops.index(origin_station_id)

            fare = query_national_rail_fare(schedule_id, fare_class, stops_travelled)
            if not fare:
                conn.rollback()
                return False, "Invalid fare class or schedule."

            available = query_available_seats(schedule_id, travel_date, fare_class)
            if not available:
                conn.rollback()
                return False, "No available seats for this schedule, date, and fare class."

            if seat_id.lower() == "any":
                selected_seat = available[0]
            else:
                selected_seat = next((seat for seat in available if seat["seat_id"] == seat_id), None)

            if not selected_seat:
                conn.rollback()
                return False, f"Seat {seat_id} is not available."

            booking_id = _gen_booking_id(cur)
            payment_id = _gen_payment_id(cur)
            now = datetime.now(timezone.utc)

            cur.execute(
                """
                INSERT INTO bookings (
                    booking_id,
                    user_id,
                    schedule_id,
                    origin_station_id,
                    destination_station_id,
                    travel_date,
                    departure_time,
                    ticket_type,
                    fare_class,
                    coach,
                    seat_id,
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
                RETURNING *;
                """,
                (
                    booking_id,
                    user_id,
                    schedule_id,
                    origin_station_id,
                    destination_station_id,
                    travel_date,
                    schedule["first_train_time"],
                    ticket_type,
                    fare_class,
                    selected_seat["coach"],
                    selected_seat["seat_id"],
                    stops_travelled,
                    fare["total_fare_usd"],
                    "confirmed",
                    now,
                ),
            )
            booking = dict(cur.fetchone())

            cur.execute(
                """
                INSERT INTO rail_payments (
                    payment_id,
                    booking_id,
                    amount_usd,
                    method,
                    status,
                    paid_at
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *;
                """,
                (
                    payment_id,
                    booking_id,
                    fare["total_fare_usd"],
                    "credit_card",
                    "paid",
                    now,
                ),
            )
            payment = dict(cur.fetchone())

            conn.commit()
            return True, {
                "booking": booking,
                "payment": payment,
            }

    except Exception as e:
        conn.rollback()
        return False, str(e)

    finally:
        conn.close()


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a national rail booking owned by the given user.

    Payment amount is kept as original paid amount.
    Refund amount is calculated and returned separately.
    """
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    b.*,
                    nrs.service_type
                FROM bookings b
                JOIN national_rail_schedules nrs ON b.schedule_id = nrs.schedule_id
                WHERE b.booking_id = %s
                  AND b.user_id = %s;
                """,
                (booking_id, user_id),
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
                booking["departure_time"] or datetime.min.time(),
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
                UPDATE bookings
                SET status = 'cancelled'
                WHERE booking_id = %s
                RETURNING *;
                """,
                (booking_id,),
            )
            updated_booking = dict(cur.fetchone())

            cur.execute(
                """
                UPDATE rail_payments
                SET status = 'refunded'
                WHERE booking_id = %s
                RETURNING *;
                """,
                (booking_id,),
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


# ── AUTHENTICATION QUERIES ────────────────────────────────────────────────────

def register_user(
    email: str,
    first_name: str,
    surname: str,
    year_of_birth: int,
    password: str,
    secret_question: str,
    secret_answer: str,
) -> tuple[bool, str]:
    """
    Register a new user.
    Returns (True, user_id) on success or (False, error_message) on failure.
    """
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

            user_id = _gen_user_id(cur)
            cur.execute(
                """
                INSERT INTO registered_users (
                    user_id,
                    full_name,
                    email,
                    password_hash,
                    phone,
                    date_of_birth,
                    secret_question,
                    secret_answer_hash,
                    registered_at,
                    is_active
                )
                VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, TRUE)
                RETURNING user_id;
                """,
                (
                    user_id,
                    full_name,
                    email,
                    _hash_value(password),
                    date_of_birth,
                    secret_question,
                    _hash_value(secret_answer),
                    datetime.now(timezone.utc),
                ),
            )

            new_user_id = cur.fetchone()["user_id"]
            conn.commit()
            return True, new_user_id

    except Exception as e:
        conn.rollback()
        return False, str(e)

    finally:
        conn.close()


def login_user(email: str, password: str) -> Optional[dict]:
    """
    Verify credentials. Returns a user dict on success or None on failure.
    """
    sql = """
        SELECT
            user_id,
            email,
            full_name,
            phone,
            date_of_birth,
            is_active,
            password_hash
        FROM registered_users
        WHERE email = %s
          AND is_active = TRUE;
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()

    if not row or not _verify_hash(password, row["password_hash"]):
        return None

    full_name = row["full_name"] or ""
    name_parts = full_name.split(" ", 1)

    return {
        "user_id": row["user_id"],
        "email": row["email"],
        "full_name": row["full_name"],
        "first_name": name_parts[0] if name_parts else "",
        "surname": name_parts[1] if len(name_parts) > 1 else "",
        "phone": row["phone"],
        "date_of_birth": row["date_of_birth"],
        "is_active": row["is_active"],
    }


def get_user_secret_question(email: str) -> Optional[str]:
    """Return the secret question for a registered email, or None if not found."""
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
    """Return True if the provided answer matches the stored secret answer."""
    sql = """
        SELECT secret_answer_hash
        FROM registered_users
        WHERE email = %s
          AND is_active = TRUE;
    """

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()

    if not row:
        return False

    return _verify_hash(answer, row[0])


def update_password(email: str, new_password: str) -> bool:
    """Update the password for a user. Returns True if the row was updated."""
    sql = """
        UPDATE registered_users
        SET password_hash = %s
        WHERE email = %s
          AND is_active = TRUE;
    """

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (_hash_value(new_password), email))
            return cur.rowcount > 0


# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────

def query_policy_vector_search(embedding: list[float], top_k: int = VECTOR_TOP_K) -> list[dict]:
    """
    Find the most relevant policy documents for a given query embedding.
    """
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
    """
    Insert a policy document with its embedding into the database.
    Used by skeleton/seed_vectors.py.
    """
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
