"""Pipecat voice pipeline: caller audio -> STT -> LLM -> TTS -> caller audio.

STT and TTS are ElevenLabs, the LLM is Google Gemini, turn-taking uses
Silero VAD. The three booking tools are registered on the LLM service so
Gemini can call them mid-conversation; every tool result and transcript
line is forwarded to the dashboard hub the same way the REST layer does.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import aiohttp

PublishFn = Callable[[dict], Awaitable[None]]


def build_function_schemas(
    db_path: str, publish: PublishFn
) -> list[Any]:
    """Tool definitions with handlers. Imported lazily so tests skip Pipecat."""
    from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
    from pipecat.services.llm_service import FunctionCallParams

    from . import tools
    from .store import pretty_when

    async def _emit(event: dict) -> None:
        try:
            await publish(event)
        except Exception:
            pass

    async def on_check_availability(params: FunctionCallParams) -> None:
        args = params.arguments
        out = tools.check_doctor_availability(
            db_path,
            doctor=str(args.get("doctor", "")),
            date=str(args.get("date", "")),
        )
        await params.result_callback(out)

    async def on_book(params: FunctionCallParams) -> None:
        args = params.arguments
        out = tools.book_appointment(
            db_path,
            phone=str(args.get("phone", "")),
            slot_id=str(args.get("slot_id", "")),
            patient_name=str(args.get("patient_name", "")),
        )
        if out.get("ok"):
            await _emit({"type": "appointments_changed", "highlight": out["appointment_id"]})
            await _emit({"type": "booking_confirmed", "appointment": out})
            await _emit(
                {
                    "type": "transcript",
                    "role": "system",
                    "text": f"Booked {out['doctor']} on {pretty_when(out['starts_at'])}",
                }
            )
        await params.result_callback(out)

    async def on_cancel(params: FunctionCallParams) -> None:
        args = params.arguments
        out = tools.cancel_appointment(
            db_path,
            phone=str(args.get("phone", "")),
            appointment_id=str(args.get("appointment_id", "")),
        )
        if out.get("ok"):
            await _emit({"type": "appointments_changed", "highlight": out["appointment_id"]})
        await params.result_callback(out)

    return [
        FunctionSchema(
            name="check_doctor_availability",
            description=(
                "Look up open appointment slots. Filter by doctor name or "
                "speciality fragment and by date YYYY-MM-DD when known."
            ),
            properties={
                "doctor": {
                    "type": "string",
                    "description": "Doctor name or speciality, e.g. 'Meera' or 'dermatology'",
                },
                "date": {
                    "type": "string",
                    "description": "Day as YYYY-MM-DD, empty when the caller gave none",
                },
            },
            required=[],
            handler=on_check_availability,
        ),
        FunctionSchema(
            name="book_appointment",
            description=(
                "Book an open slot for the caller. Use only the phone number "
                "the caller spoke and read back, plus a slot id from "
                "check_doctor_availability."
            ),
            properties={
                "phone": {"type": "string", "description": "Caller phone digits as spoken"},
                "slot_id": {"type": "string", "description": "Slot id from availability results"},
                "patient_name": {"type": "string", "description": "Caller name when given"},
            },
            required=["phone", "slot_id"],
            handler=on_book,
        ),
        FunctionSchema(
            name="cancel_appointment",
            description=(
                "Cancel the caller's booking. Use only the phone number the "
                "caller spoke and read back. Pass appointment_id when the "
                "caller picked one of several bookings."
            ),
            properties={
                "phone": {"type": "string", "description": "Caller phone digits as spoken"},
                "appointment_id": {
                    "type": "string",
                    "description": "Booking id, empty when the caller has one booking",
                },
            },
            required=["phone"],
            handler=on_cancel,
        ),
    ]


async def run_call(connection: Any, settings: Any, publish: PublishFn) -> None:
    """Run one WebRTC call. Returns when the caller hangs up."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.frames.frames import (
        BotStoppedSpeakingFrame,
        EndFrame,
        ErrorFrame,
        TextFrame,
        TranscriptionFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.observers.base_observer import BaseObserver, FramePushed
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
    from pipecat.services.elevenlabs.stt import ElevenLabsSTTService
    from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
    from pipecat.services.google.llm import GoogleLLMService
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    from .agent_prompt import SYSTEM_INSTRUCTION

    if not settings.elevenlabs_key or not settings.gemini_key:
        raise RuntimeError("missing ELEVENLABS_API_KEY or GEMINI_API_KEY")

    class DashboardObserver(BaseObserver):
        def __init__(self) -> None:
            super().__init__()
            self._last_role = ""
            self._last_text = ""
            self._agent_buffer = ""

        async def _say(self, role: str, text: str) -> None:
            text = (text or "").strip()
            if not text:
                return
            if self._last_role == role and self._last_text == text:
                return
            self._last_role, self._last_text = role, text
            try:
                await publish({"type": "transcript", "role": role, "text": text})
            except Exception:
                pass

        async def _agent_chunk(self, text: str) -> None:
            piece = text or ""
            if self._agent_buffer and piece and not self._agent_buffer.endswith((" ", "\n")):
                if not piece.startswith((" ", ",", ".", "?", "!", "'", "’", "-", ":", ";")):
                    piece = " " + piece
            self._agent_buffer += piece
            if piece and piece[-1] in ".?!":
                await self._say("agent", self._agent_buffer)
                self._agent_buffer = ""

        async def _flush_agent(self) -> None:
            if self._agent_buffer.strip():
                await self._say("agent", self._agent_buffer)
            self._agent_buffer = ""

        async def on_push_frame(self, data: FramePushed) -> None:
            frame = data.frame
            if isinstance(frame, TranscriptionFrame):
                await self._flush_agent()
                await self._say("caller", frame.text)
            elif isinstance(frame, TextFrame) and data.direction == FrameDirection.DOWNSTREAM:
                await self._agent_chunk(frame.text)
            elif isinstance(frame, BotStoppedSpeakingFrame):
                await self._flush_agent()
            elif isinstance(frame, ErrorFrame):
                try:
                    await publish({"type": "call_error", "error": str(frame.error)[:200]})
                except Exception:
                    pass

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        transport = SmallWebRTCTransport(
            connection,
            TransportParams(
                audio_in_enabled=True,
                audio_in_sample_rate=16000,
                audio_out_enabled=True,
                audio_out_sample_rate=24000,
            ),
        )
        stt = ElevenLabsSTTService(
            api_key=settings.elevenlabs_key,
            aiohttp_session=session,
            model=settings.elevenlabs_stt_model,
        )
        llm = GoogleLLMService(
            api_key=settings.gemini_key,
            model=settings.gemini_model,
        )
        tts = ElevenLabsTTSService(
            api_key=settings.elevenlabs_key,
            voice_id=settings.elevenlabs_voice_id,
            model=settings.elevenlabs_tts_model,
        )

        schemas = build_function_schemas(settings.db_path, publish)
        context = LLMContext(
            [{"role": "system", "content": SYSTEM_INSTRUCTION}],
            tools=ToolsSchema(standard_tools=schemas),
        )
        aggregators = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.5))
            ),
        )

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                aggregators.user(),
                llm,
                tts,
                transport.output(),
                aggregators.assistant(),
            ]
        )
        task = PipelineTask(
            pipeline,
            params=PipelineParams(allow_interruptions=True),
            observers=[DashboardObserver()],
        )

        @transport.event_handler("on_client_disconnected")
        async def on_disconnected(transport: Any, client: Any) -> None:
            try:
                await task.queue_frame(EndFrame())
            except Exception:
                task.cancel()

        runner = PipelineRunner()
        await runner.run(task)
