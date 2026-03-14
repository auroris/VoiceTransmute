"""
Voice activity detection using Silero VAD.
Emits streaming events: speech chunks arrive as they're captured,
not buffered until the end.
"""

from dataclasses import dataclass
import numpy as np
import torch
from silero_vad import load_silero_vad, VADIterator

import config


@dataclass
class SpeechStart:
    """Speech onset detected. Contains the first chunk of audio."""
    audio: bytes

@dataclass
class SpeechData:
    """Ongoing speech. Contains a chunk of audio."""
    audio: bytes

@dataclass
class SpeechEnd:
    """Speech offset detected. Contains the final chunk of audio."""
    audio: bytes


# Union type for VAD events
VadEvent = SpeechStart | SpeechData | SpeechEnd


class UtteranceDetector:
    def __init__(self):
        model = load_silero_vad()
        self._vad = VADIterator(
            model,
            threshold=config.VAD_THRESHOLD,
            sampling_rate=config.CAPTURE_SAMPLE_RATE,
            min_silence_duration_ms=config.SILENCE_DURATION_MS,
            speech_pad_ms=15,
        )
        self._in_speech = False

    def reset(self):
        """Reset state between utterances."""
        self._vad.reset_states()
        self._in_speech = False

    def _to_pcm_bytes(self, float_chunk: torch.Tensor) -> bytes:
        """Convert a float32 tensor back to int16 PCM bytes."""
        raw = float_chunk.numpy()
        return (raw * 32768.0).clip(-32768, 32767).astype(np.int16).tobytes()

    def process(self, pcm_int16: np.ndarray) -> list[VadEvent]:
        """
        Feed a block of int16 PCM samples (from sounddevice callback).
        Returns a list of VadEvents (may be empty, one, or several per call).
        """
        float_samples = pcm_int16.astype(np.float32) / 32768.0
        tensor = torch.from_numpy(float_samples)

        window = 512  # 32ms at 16kHz
        offset = 0
        events: list[VadEvent] = []

        while offset + window <= len(tensor):
            chunk = tensor[offset : offset + window]
            offset += window

            result = self._vad(chunk)

            if result is not None:
                if "start" in result:
                    self._in_speech = True
                    events.append(SpeechStart(audio=self._to_pcm_bytes(chunk)))
                    continue

                elif "end" in result:
                    self._in_speech = False
                    events.append(SpeechEnd(audio=self._to_pcm_bytes(chunk)))
                    self._vad.reset_states()
                    continue

            if self._in_speech:
                events.append(SpeechData(audio=self._to_pcm_bytes(chunk)))

        return events
