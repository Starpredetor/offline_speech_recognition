from __future__ import annotations

import importlib
import queue
import threading
from typing import Any, Callable


class AudioInputHandler:

    def __init__(self, sample_rate: int = 16000, channels: int = 1, device_id: int | None = None) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.device_id = device_id
        self._stream: Any | None = None
        self._stopped = threading.Event()

    @staticmethod
    def _load_sounddevice() -> Any:
        try:
            return importlib.import_module("sounddevice")
        except ModuleNotFoundError:
            raise RuntimeError("sounddevice is not installed. Install dependencies from requirements.txt")

    @classmethod
    def get_all_devices(cls) -> list[dict]:
        """Get all audio devices available in the system.
        
        Returns:
            List of device dictionaries with info
        """
        sounddevice = cls._load_sounddevice()
        try:
            devices = sounddevice.query_devices()
            if isinstance(devices, dict):
                return [devices]
            return list(devices)
        except Exception:
            return []

    @classmethod
    def find_loopback_device(cls) -> int | None:
        """Find stereo mix / loopback device for system audio capture.
        
        Returns:
            Device ID if found, None otherwise
        """
        devices = cls.get_all_devices()
        
        # Look for Stereo Mix, WASAPI Loopback, or similar
        loopback_keywords = ["stereo mix", "loopback", "what u hear", "wave out mix", "system audio"]
        
        for idx, device in enumerate(devices):
            name = str(device.get("name", "")).lower()
            if any(keyword in name for keyword in loopback_keywords):
                if int(device.get("max_input_channels", 0)) > 0:
                    return idx
        
        return None

    @classmethod
    def has_input_device(cls, device_id: int | None = None) -> tuple[bool, str]:
        sounddevice = cls._load_sounddevice()
        try:
            devices = sounddevice.query_devices()
        except Exception as exc:
            return False, f"Unable to query audio devices: {exc}"

        if device_id is not None:
            try:
                device = devices[device_id]
                if int(device.get("max_input_channels", 0)) > 0:
                    return True, str(device.get("name", "Audio Input"))
                return False, f"Device {device_id} has no input channels"
            except (IndexError, TypeError):
                return False, f"Device {device_id} not found"

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
        sounddevice = self._load_sounddevice()

        blocksize = int(self.sample_rate * (chunk_duration_ms / 1000))
        audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=50)
        self._stopped.clear()

        def _on_audio(indata, _frames, _time, status) -> None:
            if status:
                print(f"Audio input status: {status}")
            try:
                audio_queue.put_nowait(bytes(indata))
            except queue.Full:
                _ = audio_queue.get_nowait()
                audio_queue.put_nowait(bytes(indata))

        device_name = "audio input"
        if self.device_id is not None:
            devices = sounddevice.query_devices()
            if isinstance(devices, list) and 0 <= self.device_id < len(devices):
                device_name = str(devices[self.device_id].get("name", "audio input"))

        print(f"Starting audio stream from {device_name}. Press Ctrl+C to stop.")
        with sounddevice.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=blocksize,
            dtype="int16",
            channels=self.channels,
            callback=_on_audio,
            device=self.device_id,
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
