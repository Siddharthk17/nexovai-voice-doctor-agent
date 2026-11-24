"""Text-chat fallback that shares the real booking tools.

The voice pipeline is the LLM path. This endpoint exists so the dashboard
stays usable with no microphone or missing keys: same SQLite, same tools,
deterministic replies, structured events for the UI to highlight.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import tools
from .store import normalise_phone

PHONE_RE = re.compile(r"(\+?\d[\d\s\-]{8,}\d)")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class ChatSession:
    phone: str = ""
    last_slots: list[dict] = field(default_factory=list)


SESSIONS: dict[str, ChatSession] = {}


def _find_phone(text: str) -> str:
    match = PHONE_RE.search(text or "")
    if not match:
        return ""
    return normalise_phone(match.group(1))


def _looks_cancel(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in ("cancel", "refund", "reschedule", "delete my"))


def _looks_book(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in ("book", "confirm", "reserve", "schedule", "appointment for"))


def handle_message(db_path: str, session_id: str, text: str, slot_id: str = "") -> dict:
    """Run one chat turn. Returns reply plus events the dashboard consumes."""
    session = SESSIONS.setdefault(session_id or "anon", ChatSession())
    said_phone = _find_phone(text)
    if said_phone:
        session.phone = said_phone
    phone = session.phone
    events: list[dict] = []

    if _looks_cancel(text):
        if not phone:
            return {"reply": "Sure. Which phone number did you book with? Say all 10 digits.", "events": events}
        out = tools.cancel_appointment(db_path, phone)
        if out.get("needs_choice"):
            options = "; ".join(f"{a['doctor']} on {a['starts_at']}" for a in out["appointments"])
            reply = f"Found {len(out['appointments'])} bookings: {options}. Which one should I cancel?"
            return {"reply": reply, "events": events, "choices": out["appointments"], "phone": phone}
        if out["ok"]:
            events.append({"type": "appointments_changed", "highlight": out["appointment_id"]})
            return {
                "reply": f"Cancelled your visit with {out['doctor']} on {out['starts_at']}.",
                "events": events, "phone": phone,
            }
        return {"reply": f"Could not cancel: {out['error']}.", "events": events, "phone": phone}

    if slot_id or (_looks_book(text) and ("slot" in text.lower() or session.last_slots)):
        if not phone:
            return {"reply": "Got it. Which phone number should I book under? Say all 10 digits.", "events": events}
        picked = slot_id
        if not picked and len(session.last_slots) == 1:
            picked = session.last_slots[0]["slot_id"]
        if not picked:
            return {"reply": "Which time works? Pick one of the slots shown.", "events": events}
        out = tools.book_appointment(db_path, phone, picked)
        if out["ok"]:
            events.append({"type": "appointments_changed", "highlight": out["appointment_id"]})
            events.append({"type": "booking_confirmed", "appointment": out})
            return {
                "reply": f"Booked with {out['doctor']} on {out['starts_at']}. Booking {out['appointment_id']}.",
                "events": events, "phone": phone,
            }
        return {"reply": f"Could not book: {out['error']}.", "events": events, "phone": phone}

    guess_doctor = text
    for filler in ("show", "slots", "availability", "available", "doctor", "dr", "please", "for", "on", "me", "my", "need", "want", "see", "visit"):
        guess_doctor = re.sub(rf"\b{filler}\b\.?", " ", guess_doctor, flags=re.IGNORECASE)
    guess_doctor = re.sub(r"\d{4}-\d{2}-\d{2}", " ", guess_doctor).strip(" ,.")
    date_match = DATE_RE.search(text or "")
    out = tools.check_doctor_availability(db_path, doctor=guess_doctor, date=date_match.group(1) if date_match else "")
    if not out["ok"]:
        options = ", ".join(f"{d['name']} ({d['speciality']})" for d in out["doctors"])
        return {"reply": f"No match there. We have {options}. Who would you like to see?", "events": events}
    if not out["slots"]:
        return {"reply": "Nothing open for that search. Try another doctor or day.", "events": events}
    session.last_slots = out["slots"]
    first = out["slots"][0]
    reply = f"{len(out['slots'])} open. Earliest: {first['doctor']} on {first['starts_at']}."
    return {"reply": reply, "events": events, "slots": out["slots"], "phone": phone}
