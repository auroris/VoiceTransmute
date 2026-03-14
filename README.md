# VoiceTransmute

Near real-time speech-to-speech voice changer powered by the ElevenLabs API.

Captures audio from your microphone, detects speech using Silero VAD, streams it to the ElevenLabs speech-to-speech endpoint, and plays the transformed audio back through your speakers — all with minimal latency.

## Architecture

```
Microphone → [sounddevice] → VAD (Silero) → Streaming HTTP Upload → ElevenLabs STS API
                                                                           ↓
                                              Speakers ← [sounddevice] ← PCM Stream
```

Key design decisions for low latency:

- **Streaming upload** — The HTTP connection opens at speech onset and PCM audio streams into the request body as it arrives from the mic. The multipart boundary closes when speech ends, so the server already has most of the data before processing starts.
- **Connection pooling** — TCP/TLS connections to `api.elevenlabs.io` are reused across utterances, avoiding ~100-200ms of handshake overhead per request.
- **Streaming playback** — Response audio plays as soon as the first chunks arrive, not after the full response is received.
- **Decoupled pipeline** — Capture, VAD, API streaming, and playback all run independently (async tasks + dedicated threads), so nothing blocks anything else.

## Setup

```bash
# Clone and enter the project
cd voicetransmute

# Create a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

## Configuration

Copy `.env` and fill in your API key:

```
ELEVENLABS_API_KEY=sk_your_key_here
ELEVENLABS_VOICE_ID=your_voice_id      # optional, can pick at startup
ELEVENLABS_MODEL_ID=eleven_multilingual_sts_v2  # optional, can pick at startup
```

VAD tuning is in `config.py`:

| Setting | Default | Purpose |
|---------|---------|---------|
| `VAD_THRESHOLD` | `0.65` | Confidence threshold for speech detection (higher = fewer false triggers) |
| `SILENCE_DURATION_MS` | `300` | Silence duration before an utterance is finalized and sent |
| `MIN_SPEECH_DURATION_MS` | `300` | Minimum utterance length to send (filters out clicks, breaths) |

## Usage

```bash
python main.py
```

The app walks you through selecting:
1. Input device (microphone)
2. Output device (speakers/headphones)
3. Voice (fetched from your ElevenLabs account)
4. Model (only voice-conversion-capable models are shown)

Press Enter at any prompt to accept the default (first option, or the one from `.env`).

Skip the interactive prompts with CLI flags:

```bash
python main.py --input 0 --output 1 --voice <voice_id> --model <model_id>
```

### Runtime Commands

While the app is running:

- **`v`** + Enter — Switch voice mid-session
- **`q`** + Enter — Quit cleanly
- **Ctrl+C** — Also quits

## Project Structure

| File | Purpose |
|------|---------|
| `main.py` | Entry point, async run loop, wiring |
| `ui.py` | Device/voice/model pickers, runtime voice switcher |
| `api_client.py` | Streaming multipart upload to ElevenLabs STS endpoint |
| `vad.py` | Silero VAD wrapper, emits `SpeechStart`/`SpeechData`/`SpeechEnd` events |
| `playback.py` | Threaded audio output via sounddevice |
| `config.py` | Central configuration (env vars + defaults) |

## Requirements

- Python 3.11+
- An [ElevenLabs](https://elevenlabs.io) account with API access
- A microphone and speakers/headphones
