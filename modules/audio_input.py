from __future__ import annotations

import importlib
import queue
import threading
from typing import Any
from typing import Callable


class AudioInputHandler:
    """Microphone audio input wrapper using sounddevice."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._stream: Any | None = None
        self._stopped = threading.Event()

    @staticmethod
    def _load_sounddevice() -> Any:
        try:
            return importlib.import_module("sounddevice")
        except ModuleNotFoundError:
            raise RuntimeError("sounddevice is not installed. Install dependencies from requirements.txt")

    @classmethod
    def has_input_device(cls) -> tuple[bool, str]:
        sounddevice = cls._load_sounddevice()
        try:
            devices = sounddevice.query_devices()
        except Exception as exc:
            return False, f"Unable to query audio devices: {exc}"

        for device in devices:
            if int(device.get("max_input_channels", 0)) > 0:
                return True, str(device.get("name", "Microphone"))

        return False, "No input microphone device detected by sounddevice."

    def stop(self) -> None:
        self._stopped.set()
        if self._stream is not None:
            try:
                self._stream.abort(ignore_errors=True)
            except Exception:
                pass
            try:
                self._stream.close(ignore_errors=True)
            except Exception:
                pass
            self._stream = None

    def stream_chunks(
        self,
        callback: Callable[[bytes], None],
        chunk_duration_ms: int = 400,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Continuously captures mic audio and sends int16 PCM bytes to callback."""
        sounddevice = self._load_sounddevice()

        blocksize = int(self.sample_rate * (chunk_duration_ms / 1000))
        audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=50)
        self._stopped.clear()

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
        ) as stream:
            self._stream = stream
            try:
                while True:
                    if self._stopped.is_set():
                        break
                    if stop_event is not None and stop_event.is_set():
                        break
                    try:
                        chunk = audio_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    callback(chunk)
            except KeyboardInterrupt:
                print("Realtime stream stopped.")
            finally:
                self._stream = None
