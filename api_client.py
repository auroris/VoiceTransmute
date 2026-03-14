"""
Streams a captured utterance to the ElevenLabs speech-to-speech
endpoint using chunked transfer encoding.

The HTTP connection and multipart preamble are sent at speech onset.
PCM audio is streamed into the request body as it arrives from the mic.
At speech end, the multipart boundary is closed and we read the response.
"""

import asyncio
import struct
import uuid
import httpx
from typing import AsyncIterator

import config

# Persistent client — keeps the TCP/TLS connection alive between requests
_http_client: httpx.AsyncClient | None = None


async def get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


def _wav_header(sample_rate: int, channels: int, bits_per_sample: int) -> bytes:
    """
    Build a WAV header with placeholder data size (0x7FFFFFFF).
    The server will read PCM data until the stream ends.
    """
    data_size = 0x7FFFFFFF
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    # RIFF header
    header = struct.pack('<4sI4s', b'RIFF', 36 + data_size, b'WAVE')
    # fmt chunk
    header += struct.pack('<4sIHHIIHH', b'fmt ', 16, 1,  # PCM format
                          channels, sample_rate, byte_rate, block_align, bits_per_sample)
    # data chunk header
    header += struct.pack('<4sI', b'data', data_size)
    return header


async def fetch_usage() -> tuple[int, int] | None:
    """Fetch character usage from ElevenLabs. Returns (used, limit) or None on error."""
    client = await get_client()
    try:
        resp = await client.get(
            "https://api.elevenlabs.io/v1/user",
            headers={"xi-api-key": config.ELEVENLABS_API_KEY},
        )
        resp.raise_for_status()
        sub = resp.json()["subscription"]
        return sub["character_count"], sub["character_limit"]
    except Exception as e:
        print(f"  [usage] failed to fetch: {e}")
        return None


async def stream_speech_to_speech(
    audio_queue: asyncio.Queue[bytes | None],
) -> AsyncIterator[bytes]:
    """
    Stream audio to ElevenLabs STS endpoint.

    audio_queue: an asyncio.Queue where:
      - bytes items are PCM audio chunks to upload
      - None signals end of utterance

    Yields PCM response chunks (22050Hz 16-bit mono) as they arrive.
    """
    url = (
        f"{config.API_BASE}/{config.VOICE_ID}/stream"
        f"?output_format={config.OUTPUT_FORMAT}"
    )

    boundary = uuid.uuid4().hex
    content_type = f"multipart/form-data; boundary={boundary}"

    headers = {
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "content-type": content_type,
        "transfer-encoding": "chunked",
    }

    wav_header = _wav_header(config.CAPTURE_SAMPLE_RATE, config.CAPTURE_CHANNELS, 16)

    async def body_stream():
        # model_id field
        yield f"--{boundary}\r\n".encode()
        yield b'Content-Disposition: form-data; name="model_id"\r\n\r\n'
        yield f"{config.MODEL_ID}\r\n".encode()

        # audio file field — header
        yield f"--{boundary}\r\n".encode()
        yield b'Content-Disposition: form-data; name="audio"; filename="utterance.wav"\r\n'
        yield b"Content-Type: audio/wav\r\n\r\n"

        # WAV header with placeholder size
        yield wav_header

        # Stream PCM chunks as they arrive from the mic
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                break
            yield chunk

        # Close multipart
        yield f"\r\n--{boundary}--\r\n".encode()

    client = await get_client()

    async with client.stream(
        "POST", url, headers=headers, content=body_stream()
    ) as response:
        if response.status_code >= 400:
            body = await response.aread()
            raise httpx.HTTPStatusError(
                f"{response.status_code}: {body.decode(errors='replace')}",
                request=response.request,
                response=response,
            )
        async for chunk in response.aiter_bytes(chunk_size=4096):
            yield chunk
