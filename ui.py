"""
Interactive pickers for devices, voices, and models.
Also contains the runtime voice-switcher (stdin command handler).
"""

import asyncio
import sounddevice as sd
import httpx

import config


# ── Device picker ────────────────────────────────────────────

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


# ── Voice picker ─────────────────────────────────────────────

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


# ── Model picker ─────────────────────────────────────────────

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


# ── Runtime voice switcher ───────────────────────────────────

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
            loop.call_soon_threadsafe(event_queue.put_nowait, "quit")
            break
