"""
Plays raw PCM audio through the selected output device
using sounddevice's blocking OutputStream.

Runs in its own thread so the async pipeline isn't blocked.
"""

import queue
import threading
import numpy as np
import sounddevice as sd

import config


# Sentinel value to signal the playback thread to stop
_STOP = object()


class AudioPlayer:
    def __init__(self, device_index: int | None = None):
        self._device = device_index
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self):
        """Start the playback thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Signal the playback thread to finish and wait for it."""
        self._running = False
        self._queue.put(_STOP)
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def enqueue(self, pcm_bytes: bytes):
        """Add a chunk of raw PCM bytes to the playback queue."""
        self._queue.put(pcm_bytes)

    def drain_marker(self):
        """
        Put a None in the queue to signal that one utterance's worth
        of audio is complete. The playback thread can use this to
        know when it's safe to stop waiting for more data.
        """
        self._queue.put(None)

    def _run(self):
        """
        Playback loop. Opens a sounddevice OutputStream and writes
        PCM data as it arrives from the queue. This blocks until
        each write is consumed by the audio driver, which is what
        we want for backpressure.
        """
        stream = sd.RawOutputStream(
            samplerate=config.PLAYBACK_SAMPLE_RATE,
            channels=config.PLAYBACK_CHANNELS,
            dtype=config.PLAYBACK_DTYPE,
            device=self._device,
            blocksize=2205,  # 100ms at 22050Hz
        )
        stream.start()

        try:
            while self._running:
                item = self._queue.get()
                if item is _STOP:
                    break
                if item is None:
                    # End of one utterance's audio; just continue
                    continue
                stream.write(np.frombuffer(item, dtype=np.int16))
        finally:
            stream.stop()
            stream.close()