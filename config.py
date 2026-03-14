"""
Central configuration. 
Override values via environment variables or edit directly.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── ElevenLabs ──────────────────────────────────────────────
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_english_sts_v2")
API_BASE = "https://api.elevenlabs.io/v1/speech-to-speech"

# ── Audio capture (sent to ElevenLabs as pcm_s16le_16) ─────
CAPTURE_SAMPLE_RATE = 16000
CAPTURE_CHANNELS = 1
CAPTURE_DTYPE = "int16"

# ── Audio playback (received from ElevenLabs as pcm_22050) ─
PLAYBACK_SAMPLE_RATE = 22050
PLAYBACK_CHANNELS = 1
PLAYBACK_DTYPE = "int16"

# ── API output format ──────────────────────────────────────
OUTPUT_FORMAT = "pcm_22050"

# ── VAD (Silero) ───────────────────────────────────────────
VAD_THRESHOLD = 0.65
SILENCE_DURATION_MS = 300   # silence before utterance is finalized
MIN_SPEECH_DURATION_MS = 300  # ignore very short bursts