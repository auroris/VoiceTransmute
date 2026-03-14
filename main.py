"""
Main entry point. Wires together:
  - Audio capture via sounddevice
  - Voice activity detection via Silero (streaming events)
  - ElevenLabs speech-to-speech API (streaming upload)
  - Audio playback via sounddevice

Usage:
  python main.py                 (lists devices, prompts for selection)
  python main.py --input 3 --output 5   (use device indices directly)
"""

import argparse
import asyncio
import sys
import threading
import time
import numpy as np
import sounddevice as sd
import httpx

import config
from vad import UtteranceDetector, SpeechStart, SpeechData, SpeechEnd
from api_client import stream_speech_to_speech
from playback import AudioPlayer


def get_filtered_devices(direction: str) -> list[tuple[int, dict]]:
    """Return list of (real_device_index, device_info) for the given direction."""
    devices = sd.query_devices()
    is_input = direction == "input"
    return [
        (i, dev) for i, dev in enumerate(devices)
        if (dev["max_input_channels"] if is_input else dev["max_output_channels"]) > 0
    ]


def pick_device(direction: str) -> int:
    """Interactive device picker. Shows only relevant devices with clean numbering."""
    filtered = get_filtered_devices(direction)
    label = "Input" if direction == "input" else "Output"
    print(f"\n── {label} Devices ─────────────────────────────────")
    for display_idx, (_real_idx, dev) in enumerate(filtered):
        print(f"  {display_idx:3d}: {dev['name']}")
    print()

    while True:
        raw = input(f"Select {direction} device [0]: ").strip()
        if raw == "":
            return filtered[0][0]
        try:
            idx = int(raw)
            if 0 <= idx < len(filtered):
                return filtered[idx][0]
            print("  Out of range. Try again.")
        except ValueError:
            print("  Invalid index. Try again.")


def fetch_voices() -> list[dict]:
    """Fetch personal voices from ElevenLabs."""
    url = "https://api.elevenlabs.io/v2/voices"
    headers = {"xi-api-key": config.ELEVENLABS_API_KEY}
    resp = httpx.get(url, headers=headers, params={"voice_type": "personal"}, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    return [
        {"voice_id": v["voice_id"], "name": v["name"]}
        for v in data.get("voices", [])
    ]


def pick_voice(voices: list[dict]) -> str:
    """List personal voices and let the user pick one."""
    if not voices:
        print("  No personal voices found. Using configured default.")
        return config.VOICE_ID

    configured_idx = None
    for i, v in enumerate(voices):
        if v["voice_id"] == config.VOICE_ID:
            configured_idx = i

    print("\n── Your Voices ─────────────────────────────────────")
    for i, v in enumerate(voices):
        marker = " *" if v["voice_id"] == config.VOICE_ID else ""
        print(f"  {i:3d}: {v['name']}{marker}")
    print()

    default = configured_idx if configured_idx is not None else 0
    while True:
        raw = input(f"Select voice [{default}]: ").strip()
        if raw == "":
            return voices[default]["voice_id"]
        try:
            idx = int(raw)
            if 0 <= idx < len(voices):
                return voices[idx]["voice_id"]
            print("  Out of range. Try again.")
        except ValueError:
            print("  Invalid index. Try again.")


def fetch_sts_models() -> list[dict]:
    """Fetch models from ElevenLabs API that support voice conversion."""
    url = "https://api.elevenlabs.io/v1/models"
    headers = {"xi-api-key": config.ELEVENLABS_API_KEY}
    resp = httpx.get(url, headers=headers, timeout=10.0)
    resp.raise_for_status()
    models = resp.json()
    return [
        {"model_id": m["model_id"], "name": m["name"]}
        for m in models
        if m.get("can_do_voice_conversion")
    ]


def pick_model() -> str:
    """List STS-capable models and let the user pick one."""
    print("\n── Fetching available voice-conversion models... ──")
    models = fetch_sts_models()
    if not models:
        print("  No voice-conversion models found. Using default.")
        return config.MODEL_ID

    configured_idx = None
    for i, m in enumerate(models):
        if m["model_id"] == config.MODEL_ID:
            configured_idx = i

    print("\n── Voice Conversion Models ─────────────────────────")
    for i, m in enumerate(models):
        marker = " *" if m["model_id"] == config.MODEL_ID else ""
        print(f"  {i:3d}: {m['name']} ({m['model_id']}){marker}")
    print()

    default = configured_idx if configured_idx is not None else 0
    while True:
        raw = input(f"Select model [{default}]: ").strip()
        if raw == "":
            return models[default]["model_id"]
        try:
            idx = int(raw)
            if 0 <= idx < len(models):
                return models[idx]["model_id"]
            print("  Out of range. Try again.")
        except ValueError:
            print("  Invalid index. Try again.")


def voice_switcher(voices: list[dict], loop: asyncio.AbstractEventLoop,
                    event_queue: asyncio.Queue):
    """
    Background thread that reads stdin for commands.
    Type 'v' to switch voice, 'q' to quit.
    """
    while True:
        try:
            line = input().strip().lower()
        except EOFError:
            break

        if line == "v":
            print("\n── Switch Voice ────────────────────────────────────")
            for i, v in enumerate(voices):
                marker = " *" if v["voice_id"] == config.VOICE_ID else ""
                print(f"  {i:3d}: {v['name']}{marker}")
            print()

            while True:
                try:
                    raw = input("Select voice (or Enter to cancel): ").strip()
                except EOFError:
                    return
                if raw == "":
                    print("  Cancelled.")
                    break
                try:
                    idx = int(raw)
                    if 0 <= idx < len(voices):
                        config.VOICE_ID = voices[idx]["voice_id"]
                        print(f"  Voice switched to: {voices[idx]['name']}")
                        break
                    print("  Out of range. Try again.")
                except ValueError:
                    print("  Invalid index. Try again.")

        elif line == "q":
            # Push a KeyboardInterrupt-like signal to the async loop
            loop.call_soon_threadsafe(event_queue.put_nowait, "quit")
            break


async def run(input_device: int, output_device: int, voices: list[dict]):
    """Main async loop: capture → VAD events → streaming API → playback."""

    if not config.ELEVENLABS_API_KEY:
        print("ERROR: Set ELEVENLABS_API_KEY environment variable.")
        sys.exit(1)

    detector = UtteranceDetector()
    player = AudioPlayer(device_index=output_device)
    player.start()

    # VAD events are pushed here from the capture callback thread
    event_queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    # Start stdin command listener
    stdin_thread = threading.Thread(
        target=voice_switcher, args=(voices, loop, event_queue), daemon=True
    )
    stdin_thread.start()

    def audio_callback(indata: np.ndarray, frames: int, time_info, status):
        if status:
            print(f"  [capture] {status}", file=sys.stderr)

        mono = indata[:, 0]
        events = detector.process(mono)
        for event in events:
            loop.call_soon_threadsafe(event_queue.put_nowait, event)

    capture = sd.InputStream(
        samplerate=config.CAPTURE_SAMPLE_RATE,
        channels=config.CAPTURE_CHANNELS,
        dtype=config.CAPTURE_DTYPE,
        device=input_device,
        blocksize=512,
        callback=audio_callback,
    )

    print("\n── Listening. Speak into the microphone. ──")
    print("── Commands: 'v' = switch voice, 'q' = quit ──\n")
    capture.start()

    # The audio queue feeds PCM chunks into the current API request.
    # None signals end of utterance.
    audio_queue: asyncio.Queue[bytes | None] | None = None
    api_task: asyncio.Task | None = None
    speech_start_time: float = 0
    pcm_byte_count: int = 0

    async def run_api_request(q: asyncio.Queue[bytes | None]):
        """Run one streaming API request and feed results to the player."""
        try:
            chunk_count = 0
            async for audio_chunk in stream_speech_to_speech(q):
                player.enqueue(audio_chunk)
                chunk_count += 1
            player.drain_marker()
            print(f"  [playback] {chunk_count} chunks queued")
        except Exception as e:
            print(f"  [error] API request failed: {e}", file=sys.stderr)

    try:
        while True:
            event = await event_queue.get()

            if event == "quit":
                print("\n── Shutting down. ──")
                break

            if isinstance(event, SpeechStart):
                speech_start_time = time.monotonic()
                pcm_byte_count = len(event.audio)

                # Start a new streaming upload
                audio_queue = asyncio.Queue()
                audio_queue.put_nowait(event.audio)
                api_task = asyncio.create_task(run_api_request(audio_queue))
                print("  [stream] speech detected, connection opened")

            elif isinstance(event, SpeechData):
                if audio_queue is not None:
                    pcm_byte_count += len(event.audio)
                    audio_queue.put_nowait(event.audio)

            elif isinstance(event, SpeechEnd):
                if audio_queue is not None:
                    pcm_byte_count += len(event.audio)
                    audio_queue.put_nowait(event.audio)
                    audio_queue.put_nowait(None)  # signal end of audio

                    duration_ms = pcm_byte_count / (config.CAPTURE_SAMPLE_RATE * 2) * 1000
                    elapsed_ms = (time.monotonic() - speech_start_time) * 1000
                    print(f"  [stream] utterance complete: {duration_ms:.0f}ms audio, "
                          f"streamed over {elapsed_ms:.0f}ms")

                    audio_queue = None

    except KeyboardInterrupt:
        print("\n── Shutting down. ──")
    finally:
        capture.stop()
        capture.close()
        if audio_queue is not None:
            audio_queue.put_nowait(None)
        if api_task is not None:
            api_task.cancel()
        player.stop()


def main():
    parser = argparse.ArgumentParser(description="Speech-to-Speech voice changer")
    parser.add_argument("--input", type=int, default=None, help="Input device index")
    parser.add_argument("--output", type=int, default=None, help="Output device index")
    parser.add_argument("--model", type=str, default=None, help="Model ID to use")
    parser.add_argument("--voice", type=str, default=None, help="Voice ID to use")
    args = parser.parse_args()

    if not config.ELEVENLABS_API_KEY:
        print("ERROR: Set ELEVENLABS_API_KEY in .env or environment.")
        sys.exit(1)

    input_dev = args.input
    output_dev = args.output

    if input_dev is None:
        input_dev = pick_device("input")

    if output_dev is None:
        output_dev = pick_device("output")

    # Fetch voices once, reuse for startup picker and runtime switcher
    voices = fetch_voices()

    if args.voice:
        config.VOICE_ID = args.voice
    else:
        config.VOICE_ID = pick_voice(voices)

    if args.model:
        config.MODEL_ID = args.model
    else:
        config.MODEL_ID = pick_model()

    print(f"\n  Input:  {sd.query_devices(input_dev)['name']}")
    print(f"  Output: {sd.query_devices(output_dev)['name']}")
    print(f"  Voice:  {config.VOICE_ID}")
    print(f"  Model:  {config.MODEL_ID}")

    asyncio.run(run(input_dev, output_dev, voices))


if __name__ == "__main__":
    main()
