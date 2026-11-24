"""Booking tools: pure functions over SQLite, shared by voice and chat.

Each tool returns a plain dict so FastAPI, the Pipecat function handlers
and the text-chat fallback all speak the same shape. Nothing here imports
Pipecat, which keeps the unit tests fast and the agent prompt honest about
what the tools actually do.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta

from .store import connect, normalise_phone

MAX_OFFERED_SLOTS = 8

SPECIALITY_WORDS = {
    "skin": "dermatology",
    "hair": "dermatology",
    "bone": "orthopaedics",
    "joint": "orthopaedics",
    "fracture": "orthopaedics",
    "child": "paediatrics",
    "kid": "paediatrics",
    "baby": "paediatrics",
    "fever": "general physician",
    "cold": "general physician",
    "general": "general physician",
}


def _doctor_variants(query: str) -> list[str]:
    """The caller says 'skin', the board says 'dermatology'. Bridge that."""
    words = [query]
    low = query.lower()
    for plain, speciality in SPECIALITY_WORDS.items():
        if plain in low:
            words.append(speciality)
    return words


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def normalise_date(raw: str) -> tuple[str, str]:
    """Spoken day -> YYYY-MM-DD. Returns (date, error); empty date means none given."""
    text = (raw or "").strip().lower()
    if not text:
        return "", ""
    today = datetime.now().date()
    if text in ("today",):
        return today.isoformat(), ""
    if text in ("tomorrow",):
        return (today + timedelta(days=1)).isoformat(), ""
    if text in WEEKDAYS:
        delta = (WEEKDAYS.index(text) - today.weekday()) % 7 or 7
        return (today + timedelta(days=delta)).isoformat(), ""
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return text, ""
    except ValueError:
        return "", f"could not understand the day '{raw.strip()}', ask for a date like next Tuesday"


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
        variants = _doctor_variants(want)
        matched_ids = [
            d["id"]
            for d in doctors.values()
            if not want
            or any(v in d["name"].lower() or v in d["speciality"].lower() for v in variants)
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
        day, day_error = normalise_date(date)
        if day_error:
            return {"ok": False, "error": day_error}
        now = datetime.now().astimezone()
        params: list[str] = []
        where = ["status = 'open'"]
        if matched_ids:
            where.append(f"doctor_id IN ({','.join('?' * len(matched_ids))})")
            params.extend(matched_ids)
        if day:
            where.append("starts_at LIKE ?")
            params.append(f"{day}%")
        rows = conn.execute(
            f"""SELECT id, doctor_id, starts_at FROM slots
                WHERE {' AND '.join(where)} ORDER BY starts_at LIMIT 40""",
            (*params,),
        ).fetchall()
        upcoming = [
            r for r in rows if datetime.fromisoformat(r["starts_at"]) >= now
        ][:MAX_OFFERED_SLOTS]
        return {
            "ok": True,
            "slots": [
                {
                    "slot_id": r["id"],
                    "doctor": doctors[r["doctor_id"]]["name"],
                    "speciality": doctors[r["doctor_id"]]["speciality"],
                    "starts_at": r["starts_at"],
                }
                for r in upcoming
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
        if datetime.fromisoformat(slot["starts_at"]) < datetime.now().astimezone():
            return {"ok": False, "error": "that time has already passed, offer another one"}
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
            moved = conn.execute(
                "UPDATE appointments SET status = 'cancelled' WHERE id = ? AND status = 'booked'",
                (target["id"],),
            )
            if moved.rowcount == 0:
                return {"ok": False, "error": "that appointment is already cancelled"}
            still_booked = conn.execute(
                "SELECT COUNT(*) AS n FROM appointments WHERE slot_id = ? AND status = 'booked'",
                (target["slot_id"],),
            ).fetchone()["n"]
            if still_booked == 0:
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
        out = []
        for r in conn.execute(
            f"""SELECT a.id, a.starts_at, a.patient_phone, a.patient_name,
                       a.status, d.name AS doctor, d.speciality, d.room
                FROM appointments a JOIN doctors d ON d.id = a.doctor_id
                {where} ORDER BY a.starts_at"""
        ):
            row = dict(r)
            row["patient_phone"] = "••••••" + row["patient_phone"][-4:]
            out.append(row)
        return out
    finally:
        conn.close()
