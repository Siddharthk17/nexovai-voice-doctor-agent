"""Booking tools: pure functions over SQLite, shared by voice and chat.

Each tool returns a plain dict so FastAPI, the Pipecat function handlers
and the text-chat fallback all speak the same shape. Nothing here imports
Pipecat, which keeps the unit tests fast and the agent prompt honest about
what the tools actually do.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from .store import connect, normalise_phone

MAX_OFFERED_SLOTS = 8


def _doctor_rows(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {r["id"]: r for r in conn.execute("SELECT * FROM doctors")}


def check_doctor_availability(
    db_path: str, doctor: str = "", date: str = ""
) -> dict:
    """Open slots, optionally filtered by doctor/speciality text and date.

    doctor matches name or speciality as a case-insensitive fragment.
    date matches the YYYY-MM-DD prefix of the slot start time.
    """
    conn = connect(db_path)
    try:
        doctors = _doctor_rows(conn)
        want = (doctor or "").strip().lower()
        matched_ids = [
            d["id"]
            for d in doctors.values()
            if not want or want in d["name"].lower() or want in d["speciality"].lower()
        ]
        if want and not matched_ids:
            return {
                "ok": False,
                "error": f"no doctor or speciality matches '{doctor.strip()}'",
                "doctors": [
                    {"name": d["name"], "speciality": d["speciality"]}
                    for d in doctors.values()
                ],
            }
        params: list[str] = []
        where = ["status = 'open'"]
        if matched_ids:
            where.append(f"doctor_id IN ({','.join('?' * len(matched_ids))})")
            params.extend(matched_ids)
        if date.strip():
            where.append("starts_at LIKE ?")
            params.append(f"{date.strip()}%")
        rows = conn.execute(
            f"""SELECT id, doctor_id, starts_at FROM slots
                WHERE {' AND '.join(where)} ORDER BY starts_at LIMIT ?""",
            (*params, MAX_OFFERED_SLOTS),
        ).fetchall()
        return {
            "ok": True,
            "slots": [
                {
                    "slot_id": r["id"],
                    "doctor": doctors[r["doctor_id"]]["name"],
                    "speciality": doctors[r["doctor_id"]]["speciality"],
                    "starts_at": r["starts_at"],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


def book_appointment(
    db_path: str, phone: str, slot_id: str, patient_name: str = ""
) -> dict:
    """Book one open slot. Caller identity is the spoken phone number only."""
    caller = normalise_phone(phone)
    if not caller:
        return {"ok": False, "error": "phone number was unclear, ask the caller to repeat it digit by digit"}
    if not slot_id.strip():
        return {"ok": False, "error": "no slot selected yet"}

    conn = connect(db_path)
    try:
        slot = conn.execute(
            "SELECT id, doctor_id, starts_at, status FROM slots WHERE id = ?",
            (slot_id.strip(),),
        ).fetchone()
        if slot is None:
            return {"ok": False, "error": f"slot '{slot_id.strip()}' does not exist"}
        if slot["status"] != "open":
            return {"ok": False, "error": "that slot was just taken, offer another one"}
        doctors = _doctor_rows(conn)
        appointment_id = f"a-{uuid.uuid4().hex[:8]}"
        created = datetime.now().isoformat(timespec="seconds")
        with conn:
            conn.execute(
                "UPDATE slots SET status = 'booked' WHERE id = ? AND status = 'open'",
                (slot["id"],),
            )
            if conn.total_changes == 0:
                return {"ok": False, "error": "that slot was just taken, offer another one"}
            conn.execute(
                """INSERT INTO appointments
                   (id, doctor_id, slot_id, starts_at, patient_phone, patient_name, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'booked', ?)""",
                (
                    appointment_id,
                    slot["doctor_id"],
                    slot["id"],
                    slot["starts_at"],
                    caller,
                    patient_name.strip(),
                    created,
                ),
            )
        return {
            "ok": True,
            "appointment_id": appointment_id,
            "doctor": doctors[slot["doctor_id"]]["name"],
            "starts_at": slot["starts_at"],
            "phone": caller,
        }
    finally:
        conn.close()


def cancel_appointment(
    db_path: str, phone: str, appointment_id: str = ""
) -> dict:
    """Cancel the caller's booking, found by spoken phone number."""
    caller = normalise_phone(phone)
    if not caller:
        return {"ok": False, "error": "phone number was unclear, ask the caller to repeat it digit by digit"}

    conn = connect(db_path)
    try:
        if appointment_id.strip():
            row = conn.execute(
                """SELECT id, doctor_id, slot_id, starts_at, status FROM appointments
                   WHERE id = ? AND patient_phone = ?""",
                (appointment_id.strip(), caller),
            ).fetchone()
            if row is None:
                return {"ok": False, "error": "no booking with that id under this number"}
            if row["status"] == "cancelled":
                return {"ok": False, "error": "that appointment is already cancelled"}
            target = row
        else:
            active = conn.execute(
                """SELECT a.id, d.name AS doctor, a.starts_at FROM appointments a
                   JOIN doctors d ON d.id = a.doctor_id
                   WHERE a.patient_phone = ? AND a.status = 'booked'
                   ORDER BY a.starts_at""",
                (caller,),
            ).fetchall()
            if len(active) == 0:
                return {"ok": False, "error": "no active bookings under this number"}
            if len(active) > 1:
                return {
                    "ok": False,
                    "needs_choice": True,
                    "error": "several bookings found, ask which one to cancel",
                    "appointments": [dict(r) for r in active],
                }
            target = conn.execute(
                "SELECT id, doctor_id, slot_id, starts_at, status FROM appointments WHERE id = ?",
                (active[0]["id"],),
            ).fetchone()
        assert target is not None
        with conn:
            conn.execute(
                "UPDATE appointments SET status = 'cancelled' WHERE id = ?",
                (target["id"],),
            )
            conn.execute(
                "UPDATE slots SET status = 'open' WHERE id = ?", (target["slot_id"],)
            )
        doctors = _doctor_rows(conn)
        return {
            "ok": True,
            "appointment_id": target["id"],
            "doctor": doctors[target["doctor_id"]]["name"],
            "starts_at": target["starts_at"],
        }
    finally:
        conn.close()


def list_doctors(db_path: str) -> list[dict]:
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM doctors ORDER BY name")]
    finally:
        conn.close()


def list_appointments(db_path: str, include_cancelled: bool = False) -> list[dict]:
    conn = connect(db_path)
    try:
        where = "" if include_cancelled else "WHERE a.status = 'booked'"
        return [
            dict(r)
            for r in conn.execute(
                f"""SELECT a.id, a.starts_at, a.patient_phone, a.patient_name,
                           a.status, d.name AS doctor, d.speciality, d.room
                    FROM appointments a JOIN doctors d ON d.id = a.doctor_id
                    {where} ORDER BY a.starts_at"""
            )
        ]
    finally:
        conn.close()
