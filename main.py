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

import config
from vad import UtteranceDetector, SpeechStart, SpeechData, SpeechEnd
from api_client import stream_speech_to_speech
from playback import AudioPlayer
from ui import pick_device, fetch_voices, pick_voice, pick_model, voice_switcher, save_selections


async def run(input_device: int, output_device: int, voices: list[dict]):
    """Main async loop: capture → VAD events → streaming API → playback."""

    detector = UtteranceDetector()
    player = AudioPlayer(device_index=output_device)
    player.start()

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

    audio_queue: asyncio.Queue[bytes | None] | None = None
    api_task: asyncio.Task | None = None
    speech_start_time: float = 0
    pcm_byte_count: int = 0

    async def run_api_request(q: asyncio.Queue[bytes | None]):
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
                    audio_queue.put_nowait(None)

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

    input_dev = args.input if args.input is not None else pick_device("input")
    output_dev = args.output if args.output is not None else pick_device("output")

    voices = fetch_voices()
    config.VOICE_ID = args.voice if args.voice else pick_voice(voices)
    config.MODEL_ID = args.model if args.model else pick_model()

    input_name = sd.query_devices(input_dev)['name']
    output_name = sd.query_devices(output_dev)['name']

    print(f"\n  Input:  {input_name}")
    print(f"  Output: {output_name}")
    print(f"  Voice:  {config.VOICE_ID}")
    print(f"  Model:  {config.MODEL_ID}")

    # Remember selections for next launch
    save_selections(input_name, output_name, config.VOICE_ID, config.MODEL_ID)

    asyncio.run(run(input_dev, output_dev, voices))


if __name__ == "__main__":
    main()
