"""Runtime settings. Keys come from the environment, never from code."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    elevenlabs_key: str
    elevenlabs_voice_id: str
    elevenlabs_tts_model: str
    elevenlabs_stt_model: str
    gemini_key: str
    gemini_model: str
    port: int
    db_path: str
    frontend_origin: str

    @property
    def voice_ready(self) -> bool:
        return bool(self.elevenlabs_key and self.gemini_key)


def load_settings() -> Settings:
    return Settings(
        elevenlabs_key=os.getenv("ELEVENLABS_API_KEY", ""),
        elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL"),
        elevenlabs_tts_model=os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"),
        elevenlabs_stt_model=os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2"),
        gemini_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        port=int(os.getenv("PORT", "7860")),
        db_path=os.getenv("DB_PATH", "./appoint.db"),
        frontend_origin=os.getenv("FRONTEND_ORIGIN", "http://localhost:5173"),
    )
