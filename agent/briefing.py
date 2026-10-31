"""Desk sign-off spoken audio briefing via Gemini 3.8 Flash TTS with WAV fallback (Section 8.4)."""

from __future__ import annotations

import asyncio
import base64
import io
import math
import struct
import wave
from pathlib import Path
from typing import Any

from .. import config
from ..events import event_bus


def _wrap_pcm_as_wav(pcm_bytes: bytes, sample_rate: int = 24000) -> bytes:
    """Wrap raw 16-bit mono PCM bytes in a RIFF WAV container if not already a WAV file."""
    if pcm_bytes.startswith(b"RIFF"):
        return pcm_bytes
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def synthesize_fallback_wav(duration_sec: float = 2.4, sample_rate: int = 24000) -> bytes:
    """Synthesize a clean desk sign-off chime WAV buffer so `/api/briefing.wav` always serves valid audio."""
    n_samples = int(duration_sec * sample_rate)
    frames = bytearray(n_samples * 2)
    notes = (523.25, 659.25, 783.99, 1046.50)  # C5, E5, G5, C6

    for i in range(n_samples):
        t = i / sample_rate
        note_idx = min(len(notes) - 1, int(t / (duration_sec / len(notes))))
        freq = notes[note_idx]
        local_t = t - note_idx * (duration_sec / len(notes))
        env = math.exp(-3.5 * local_t) * min(1.0, local_t / 0.015)
        val = 0.25 * env * (math.sin(2.0 * math.pi * freq * t) + 0.3 * math.sin(4.0 * math.pi * freq * t))
        sample_int = max(-32767, min(32767, int(val * 32767)))
        struct.pack_into("<h", frames, i * 2, sample_int)

    return _wrap_pcm_as_wav(bytes(frames), sample_rate=sample_rate)


def _call_tts_sync(briefing_text: str) -> tuple[bytes, str]:
    """Call `gemini-3.8-flash-tts` via the Interactions API (with optional voice replication)."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=config.get_gemini_api_key(),
        http_options=types.HttpOptions(timeout=45_000),
    )
    voice_sample_path = config.get_voice_sample()
    if voice_sample_path and Path(voice_sample_path).is_file():
        sample_bytes = Path(voice_sample_path).read_bytes()
        b64_sample = base64.b64encode(sample_bytes).decode("ascii")
        interaction = client.interactions.create(
            model=config.MODEL_TTS,
            input=[
                {"type": "text", "text": briefing_text},
                {"type": "audio", "data": b64_sample, "mime_type": "audio/wav"},
            ],
            response_modalities=["AUDIO"],
            generation_config={
                "speech_config": {
                    "voice_config": {
                        "replicated_voice_config": {
                            "voice_sample_rate": 24000,
                        }
                    }
                }
            },
        )
        source_mode = "replicated_tts"
    else:
        interaction = client.interactions.create(
            model=config.MODEL_TTS,
            input=briefing_text,
            response_modalities=["AUDIO"],
            generation_config={
                "speech_config": {
                    "voice_config": {
                        "prebuilt_voice_config": {
                            "voice_name": "Kore",
                        }
                    }
                }
            },
        )
        source_mode = "prebuilt_tts"

    for output in getattr(interaction, "outputs", []) or []:
        if getattr(output, "type", None) == "audio" and getattr(output, "data", None):
            raw_data = output.data
            audio_bytes = base64.b64decode(raw_data) if isinstance(raw_data, str) else bytes(raw_data)
            return _wrap_pcm_as_wav(audio_bytes, sample_rate=24000), source_mode

    raise RuntimeError("No audio output returned from gemini-3.8-flash-tts.")


class BriefingStore:
    """In-memory store for the latest generated briefing audio and metadata."""

    def __init__(self) -> None:
        self.wav_bytes: bytes = synthesize_fallback_wav()
        self.briefing_text: str = ""
        self.source: str = "fallback_chime"
        self.voice_consented: bool = config.has_voice_sample()

    async def generate(self, briefing_text: str) -> dict[str, Any]:
        self.briefing_text = briefing_text
        self.voice_consented = config.has_voice_sample()

        if config.is_mock_llm() or not config.has_gemini_api_key():
            self.wav_bytes = synthesize_fallback_wav()
            self.source = "mock_wav"
            return {
                "source": self.source,
                "voice_consented": self.voice_consented,
                "use_browser_speech": True,
                "briefing_text": self.briefing_text,
            }

        try:
            wav_bytes, source_mode = await asyncio.to_thread(_call_tts_sync, briefing_text)
            self.wav_bytes = wav_bytes
            self.source = source_mode
            return {
                "source": self.source,
                "voice_consented": self.voice_consented,
                "use_browser_speech": False,
                "briefing_text": self.briefing_text,
            }
        except Exception as exc:  # noqa: BLE001
            event_bus.emit(
                "model_error",
                stage="tts",
                message=f"TTS synthesis unavailable ({type(exc).__name__}); falling back to chime + browser speechSynthesis.",
                fallback=True,
            )
            self.wav_bytes = synthesize_fallback_wav()
            self.source = "fallback_speech"
            return {
                "source": self.source,
                "voice_consented": self.voice_consented,
                "use_browser_speech": True,
                "briefing_text": self.briefing_text,
            }


briefing_store = BriefingStore()
