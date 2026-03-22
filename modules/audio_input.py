from __future__ import annotations

import importlib
import queue
from typing import Callable


class AudioInputHandler:
    """Microphone audio input wrapper using sounddevice."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels

    def stream_chunks(self, callback: Callable[[bytes], None], chunk_duration_ms: int = 400) -> None:
        """Continuously captures mic audio and sends int16 PCM bytes to callback."""
        try:
            sounddevice = importlib.import_module("sounddevice")
        except ModuleNotFoundError:
            raise RuntimeError("sounddevice is not installed. Install dependencies from requirements.txt")

        blocksize = int(self.sample_rate * (chunk_duration_ms / 1000))
        audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=50)

        def _on_audio(indata, _frames, _time, status) -> None:  # pragma: no cover
            if status:
                print(f"Audio input status: {status}")
            try:
                audio_queue.put_nowait(bytes(indata))
            except queue.Full:
                # Drop oldest queued chunk to keep latency bounded.
                _ = audio_queue.get_nowait()
                audio_queue.put_nowait(bytes(indata))

        print("Starting microphone stream. Press Ctrl+C to stop.")
        with sounddevice.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=blocksize,
            dtype="int16",
            channels=self.channels,
            callback=_on_audio,
        ):
            try:
                while True:
                    chunk = audio_queue.get()
                    callback(chunk)
            except KeyboardInterrupt:
                print("Realtime stream stopped.")
