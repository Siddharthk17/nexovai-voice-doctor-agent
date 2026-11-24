"""SQLite store for doctors, slots and appointments.

One file, no ORM. All writes go through this module so booking stays
atomic: a slot flips to booked and the appointment row appears together.
Phone numbers are normalised to last-10-digits on the way in, because
callers say them in many formats and the agent must match them anyway.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS doctors (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    speciality TEXT NOT NULL,
    room TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slots (
    id TEXT PRIMARY KEY,
    doctor_id TEXT NOT NULL REFERENCES doctors(id),
    starts_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,
    doctor_id TEXT NOT NULL REFERENCES doctors(id),
    slot_id TEXT NOT NULL REFERENCES slots(id),
    starts_at TEXT NOT NULL,
    patient_phone TEXT NOT NULL,
    patient_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'booked',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_slots_doctor_time ON slots(doctor_id, starts_at);
CREATE INDEX IF NOT EXISTS idx_appt_phone ON appointments(patient_phone, status);
"""

SEED_DOCTORS = [
    ("d-meera", "Dr. Meera Nair", "General Physician", "Room 101"),
    ("d-arjun", "Dr. Arjun Rao", "Dermatology", "Room 203"),
    ("d-kabir", "Dr. Kabir Shah", "Orthopaedics", "Room 305"),
    ("d-isha", "Dr. Isha Verma", "Paediatrics", "Room 107"),
]

SLOT_HOURS = [9, 10, 11, 16, 17, 18]


def normalise_phone(raw: str) -> str:
    """Keep digits only, then the last 10. Empty string when nothing usable."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) < 10:
        return ""
    return digits[-10:]


def pretty_when(iso: str) -> str:
    """'2026-11-25T09:00:00+05:30' -> 'Wed 25 Nov, 9:00 am'. Never raises."""
    try:
        moment = datetime.fromisoformat(iso)
        return moment.strftime("%a %-d %b, %-I:%M %p").replace("AM", "am").replace("PM", "pm")
    except ValueError:
        return iso


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        existing = conn.execute("SELECT COUNT(*) AS n FROM doctors").fetchone()["n"]
        if existing == 0:
            conn.executemany(
                "INSERT INTO doctors (id, name, speciality, room) VALUES (?, ?, ?, ?)",
                SEED_DOCTORS,
            )
            seed_slots(conn)
        conn.commit()
    finally:
        conn.close()


def seed_slots(conn: sqlite3.Connection) -> None:
    """Three working days of slots, mornings and evenings, IST wall-clock."""
    base = datetime.now(timezone.utc).astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    rows: list[tuple[str, str, str]] = []
    for day_offset in range(1, 4):
        day = base + timedelta(days=day_offset)
        for doctor_id, _, _, _ in SEED_DOCTORS:
            for hour in SLOT_HOURS:
                starts = day.replace(hour=hour, minute=0)
                slot_id = f"s-{doctor_id}-{starts.strftime('%m%d-%H%M')}"
                rows.append((slot_id, doctor_id, starts.isoformat()))
    conn.executemany(
        "INSERT OR IGNORE INTO slots (id, doctor_id, starts_at) VALUES (?, ?, ?)",
        rows,
    )
