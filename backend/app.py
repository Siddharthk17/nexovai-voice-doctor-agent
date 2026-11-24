"""FastAPI front door: REST, dashboard events, chat fallback, WebRTC signalling.

Voice media flows browser <-> Pipecat SmallWebRTC transport. Everything the
dashboard shows flows through here: doctors, slots, appointments, live
transcript and booking events over /ws/dashboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
        payload = json.dumps(event)
        targets = list(self.sockets)

        async def send(sock: WebSocket) -> None:
            try:
                await asyncio.wait_for(sock.send_text(payload), timeout=2)
            except Exception:
                self.sockets.discard(sock)

        await asyncio.gather(*(send(sock) for sock in targets))


hub = Hub()
logger = logging.getLogger("nexovai.desk")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    init_db(settings.db_path)
    app.state.settings = settings
    yield


app = FastAPI(title="NexovAI Care voice desk", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.getenv("FRONTEND_ORIGIN", "http://localhost:5173"),
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
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


class OfferIn(BaseModel):
    sdp: str
    type: str = "offer"
    pc_id: str | None = None


_webrtc_handler = None


def webrtc_handler():
    global _webrtc_handler
    if _webrtc_handler is None:
        from pipecat.transports.smallwebrtc.request_handler import (
            SmallWebRTCRequestHandler,
        )

        _webrtc_handler = SmallWebRTCRequestHandler()
    return _webrtc_handler


@app.post("/api/call/offer")
async def call_offer(body: OfferIn):
    """WebRTC signalling: browser posts its SDP offer, gets an answer."""
    from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequest

    from . import pipeline as voice

    s = settings()
    if not s.voice_ready:
        return {"ok": False, "error": "voice keys missing, use text chat below instead"}

    async def on_connection(connection) -> None:
        async def publish(event: dict) -> None:
            try:
                await hub.publish(event)
            except Exception:
                pass

        pipeline_task = asyncio.get_running_loop().create_task(
            voice.run_call(connection, s, publish)
        )

        def _log_failure(task: asyncio.Task) -> None:
            if task.cancelled() or task.exception() is None:
                return
            logger.error("voice pipeline failed: %r", task.exception())
            asyncio.get_running_loop().create_task(
                hub.publish({"type": "call_error", "error": "voice pipeline stopped"})
            )

        pipeline_task.add_done_callback(_log_failure)
        await hub.publish({"type": "call_started", "pc_id": connection.pc_id})

    answer = await webrtc_handler().handle_web_request(
        SmallWebRTCRequest(sdp=body.sdp, type=body.type, pc_id=body.pc_id),
        on_connection,
    )
    if not answer:
        return {"ok": False, "error": "webrtc negotiation failed"}
    await hub.publish(
        {
            "type": "transcript",
            "role": "system",
            "text": "Connected. Say hello — the desk is listening.",
        }
    )
    return {"ok": True, **answer}


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
    DIST_RESOLVED = DIST.resolve()
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        target = (DIST / full_path).resolve()
        if full_path and target.is_file() and target.is_relative_to(DIST_RESOLVED):
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
