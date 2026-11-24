import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import store, tools


def fresh_db() -> str:
    path = tempfile.mktemp(suffix=".db")
    store.init_db(path)
    return path


def test_seed_has_doctors_and_slots():
    db = fresh_db()
    assert len(tools.list_doctors(db)) == 4
    found = tools.check_doctor_availability(db)
    assert found["ok"] and len(found["slots"]) > 0


def test_phone_normalises_to_last_ten():
    assert store.normalise_phone("+91 98765 43210") == "9876543210"
    assert store.normalise_phone("098765 43210") == "9876543210"
    assert store.normalise_phone("123") == ""


def test_book_then_cancel_roundtrip():
    db = fresh_db()
    slot = tools.check_doctor_availability(db)["slots"][0]
    booked = tools.book_appointment(db, "98765 43210", slot["slot_id"], "Asha")
    assert booked["ok"], booked
    assert booked["phone"] == "9876543210"

    double = tools.book_appointment(db, "9999999999", slot["slot_id"])
    assert not double["ok"]

    cancelled = tools.cancel_appointment(db, "+91-98765-43210", booked["appointment_id"])
    assert cancelled["ok"], cancelled
    again = tools.cancel_appointment(db, "9876543210", booked["appointment_id"])
    assert not again["ok"]


def test_book_rejects_bad_phone_and_unknown_slot():
    db = fresh_db()
    assert not tools.book_appointment(db, "123", "s-nope")["ok"]
    assert not tools.book_appointment(db, "9876543210", "s-nope")["ok"]


def test_cancel_with_several_booking_asks_which():
    db = fresh_db()
    slots = tools.check_doctor_availability(db)["slots"]
    assert len(slots) >= 2
    tools.book_appointment(db, "9876543210", slots[0]["slot_id"])
    tools.book_appointment(db, "9876543210", slots[1]["slot_id"])
    out = tools.cancel_appointment(db, "9876543210")
    assert not out["ok"] and out.get("needs_choice")


def test_availability_unknown_doctor_lists_options():
    db = fresh_db()
    out = tools.check_doctor_availability(db, doctor="neurologist")
    assert not out["ok"] and len(out["doctors"]) == 4


def test_availability_filters_by_date():
    db = fresh_db()
    first = tools.check_doctor_availability(db)["slots"][0]
    day = first["starts_at"][:10]
    out = tools.check_doctor_availability(db, date=day)
    assert out["ok"] and all(s["starts_at"].startswith(day) for s in out["slots"])


def test_plain_words_match_specialities():
    db = fresh_db()
    assert tools.check_doctor_availability(db, doctor="skin")["slots"]
    assert all(
        s["speciality"] == "Dermatology"
        for s in tools.check_doctor_availability(db, doctor="skin")["slots"]
    )
    assert tools.check_doctor_availability(db, doctor="child")["slots"]


def test_pretty_when_keeps_bad_input():
    assert store.pretty_when("2026-11-25T09:00:00+05:30") == "Wed 25 Nov, 9:00 am"
    assert store.pretty_when("nope") == "nope"
