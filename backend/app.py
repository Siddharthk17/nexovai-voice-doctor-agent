"""FastAPI front door: REST, dashboard events, chat fallback, WebRTC signalling.

Voice media flows browser <-> Pipecat SmallWebRTC transport. Everything the
dashboard shows flows through here: doctors, slots, appointments, live
transcript and booking events over /ws/dashboard.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from . import chat, tools
from .config import load_settings
from .store import init_db


class Hub:
    """Fan-out for dashboard sockets. Best-effort: a slow client never blocks."""

    def __init__(self) -> None:
        self.sockets: set[WebSocket] = set()

    async def publish(self, event: dict) -> None:
        dead: list[WebSocket] = []
        payload = json.dumps(event)
        for sock in self.sockets:
            try:
                await asyncio.wait_for(sock.send_text(payload), timeout=2)
            except Exception:
                dead.append(sock)
        for sock in dead:
            self.sockets.discard(sock)


hub = Hub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    init_db(settings.db_path)
    app.state.settings = settings
    yield


app = FastAPI(title="NexovAI Care voice desk", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class BookIn(BaseModel):
    phone: str
    slot_id: str
    patient_name: str = ""


class CancelIn(BaseModel):
    phone: str
    appointment_id: str = ""


class ChatIn(BaseModel):
    session_id: str = "anon"
    message: str = ""
    slot_id: str = ""


def settings():
    return app.state.settings


@app.get("/api/health")
def health():
    s = settings()
    missing = []
    if not s.elevenlabs_key:
        missing.append("ELEVENLABS_API_KEY")
    if not s.gemini_key:
        missing.append("GEMINI_API_KEY")
    return {
        "voice_ready": s.voice_ready,
        "missing": missing,
        "doctors": len(tools.list_doctors(s.db_path)),
        "active_appointments": len(tools.list_appointments(s.db_path)),
    }


@app.get("/api/doctors")
def doctors():
    s = settings()
    out = []
    for doc in tools.list_doctors(s.db_path):
        found = tools.check_doctor_availability(s.db_path, doctor=doc["name"])
        out.append({**doc, "open_slots": found.get("slots", [])[:6]})
    return {"doctors": out}


@app.get("/api/appointments")
def appointments(include_cancelled: bool = False):
    s = settings()
    return {"appointments": tools.list_appointments(s.db_path, include_cancelled)}


@app.post("/api/book")
async def book(body: BookIn):
    s = settings()
    out = tools.book_appointment(s.db_path, body.phone, body.slot_id, body.patient_name)
    if out.get("ok"):
        await hub.publish({"type": "appointments_changed", "highlight": out["appointment_id"]})
        await hub.publish({"type": "booking_confirmed", "appointment": out})
    return out


@app.post("/api/cancel")
async def cancel(body: CancelIn):
    s = settings()
    out = tools.cancel_appointment(s.db_path, body.phone, body.appointment_id)
    if out.get("ok"):
        await hub.publish({"type": "appointments_changed", "highlight": out["appointment_id"]})
    return out


@app.post("/api/chat")
async def chat_turn(body: ChatIn):
    s = settings()
    out = chat.handle_message(s.db_path, body.session_id, body.message, body.slot_id)
    for event in out.get("events", []):
        await hub.publish(event)
    if out.get("phone"):
        await hub.publish({"type": "caller_known", "phone": out["phone"]})
    return out


@app.websocket("/ws/dashboard")
async def dashboard_socket(sock: WebSocket):
    await sock.accept()
    hub.sockets.add(sock)
    try:
        while True:
            await sock.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.sockets.discard(sock)


DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        target = DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
