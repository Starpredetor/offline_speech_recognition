from __future__ import annotations

import importlib
import inspect
import queue
import threading
from typing import Any, Callable

import numpy as np


PREFERRED_SYSTEM_DEVICE_HINTS = [
    "speakers (realtek",
    "realtek(r) audio",
]


class AudioInputHandler:

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        device_id: int | None = None,
        capture_sample_rate: int | None = None,
        use_wasapi_loopback: bool = False,
    ) -> None:
        self.sample_rate = sample_rate
        self.capture_sample_rate = capture_sample_rate or sample_rate
        self.channels = channels
        self.device_id = device_id
        self.use_wasapi_loopback = use_wasapi_loopback
        self._stream: Any | None = None
        self._stopped = threading.Event()

    def _normalize_audio_chunk(self, chunk: bytes) -> bytes:
        pcm = np.frombuffer(chunk, dtype=np.int16)
        if pcm.size == 0:
            return chunk

        if self.channels > 1:
            frame_count = pcm.size // self.channels
            if frame_count == 0:
                return chunk
            trimmed = pcm[: frame_count * self.channels]
            mono = trimmed.reshape(frame_count, self.channels).mean(axis=1).astype(np.int16)
        else:
            mono = pcm

        if self.capture_sample_rate == self.sample_rate or mono.size == 0:
            return mono.tobytes()

        source_positions = np.arange(mono.size, dtype=np.float32)
        target_length = max(1, int(round(mono.size * self.sample_rate / self.capture_sample_rate)))
        target_positions = np.linspace(0, max(0, mono.size - 1), target_length, dtype=np.float32)
        resampled = np.interp(target_positions, source_positions, mono.astype(np.float32))
        return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()

    @staticmethod
    def _load_sounddevice() -> Any:
        try:
            return importlib.import_module("sounddevice")
        except ModuleNotFoundError:
            raise RuntimeError("sounddevice is not installed. Install dependencies from requirements.txt")

    @staticmethod
    def _load_soundcard() -> Any:
        try:
            return importlib.import_module("soundcard")
        except ModuleNotFoundError:
            raise RuntimeError("soundcard is not installed. Install dependencies from requirements.txt")

    @classmethod
    def get_all_devices(cls) -> list[dict]:
        sounddevice = cls._load_sounddevice()
        try:
            devices = sounddevice.query_devices()
            if isinstance(devices, dict):
                return [devices]
            return list(devices)
        except Exception:
            return []

    @classmethod
    def _get_hostapi_name(cls, device: dict) -> str:
        sounddevice = cls._load_sounddevice()
        try:
            hostapis = sounddevice.query_hostapis()
            hostapi_index = int(device.get("hostapi", -1))
            if 0 <= hostapi_index < len(hostapis):
                return str(hostapis[hostapi_index].get("name", ""))
        except Exception:
            return ""
        return ""

    @classmethod
    def get_default_device_ids(cls) -> tuple[int | None, int | None]:
        sounddevice = cls._load_sounddevice()
        devices = cls.get_all_devices()

        def _match_device_index(target: Any) -> int | None:
            if not isinstance(target, dict):
                return None
            target_name = str(target.get("name", ""))
            target_hostapi = target.get("hostapi")
            for idx, dev in enumerate(devices):
                if str(dev.get("name", "")) != target_name:
                    continue
                if target_hostapi is None or dev.get("hostapi") == target_hostapi:
                    return idx
            for idx, dev in enumerate(devices):
                if str(dev.get("name", "")) == target_name:
                    return idx
            return None

        try:
            default_devices = sounddevice.default.device
            if isinstance(default_devices, (list, tuple)) and len(default_devices) >= 2:
                in_id = int(default_devices[0]) if default_devices[0] is not None and int(default_devices[0]) >= 0 else None
                out_id = int(default_devices[1]) if default_devices[1] is not None and int(default_devices[1]) >= 0 else None
                if in_id is not None or out_id is not None:
                    return in_id, out_id
        except Exception:
            pass

        in_id = None
        out_id = None
        try:
            in_dev = sounddevice.query_devices(kind="input")
            in_id = _match_device_index(in_dev)
        except Exception:
            pass
        try:
            out_dev = sounddevice.query_devices(kind="output")
            out_id = _match_device_index(out_dev)
        except Exception:
            pass

        if in_id is not None or out_id is not None:
            return in_id, out_id
        return None, None

    @classmethod
    def get_system_audio_candidates(cls) -> list[dict]:
        devices = cls.get_all_devices()
        ranked: list[dict] = []
        default_in_id, default_out_id = cls.get_default_device_ids()

        loopback_keywords = ["stereo mix", "loopback", "what u hear", "wave out mix", "system audio"]

        for idx, device in enumerate(devices):
            name = str(device.get("name", ""))
            name_l = name.lower()
            max_in = int(device.get("max_input_channels", 0))
            max_out = int(device.get("max_output_channels", 0))
            default_samplerate = int(round(float(device.get("default_samplerate", 16000) or 16000)))
            hostapi_name = cls._get_hostapi_name(device).lower()

            # Path A: classic input loopback devices like Stereo Mix
            if max_in > 0 and any(keyword in name_l for keyword in loopback_keywords):
                score = 70
                if "stereo mix" in name_l:
                    score += 10
                if "realtek" in name_l:
                    score += 5
                if "mapper" in name_l or name_l.endswith("()"):
                    score -= 8
                if default_in_id is not None and idx == default_in_id:
                    score += 20
                ranked.append(
                    {
                        "device_id": idx,
                        "name": name,
                        "use_wasapi_loopback": False,
                        "channels": max(1, min(max_in, 2)),
                        "sample_rate": default_samplerate,
                        "score": score,
                    }
                )

            # Path B: WASAPI loopback from output devices (works on many systems without Stereo Mix)
            if max_out > 0 and "wasapi" in hostapi_name:
                score = 60
                if "speaker" in name_l or "headphone" in name_l:
                    score += 8
                if "mapper" in name_l or "primary sound" in name_l:
                    score -= 12
                if default_out_id is not None and idx == default_out_id:
                    score += 25
                if any(hint in name_l for hint in PREFERRED_SYSTEM_DEVICE_HINTS):
                    score += 120
                ranked.append(
                    {
                        "device_id": idx,
                        "name": name,
                        "use_wasapi_loopback": True,
                        "channels": max(1, min(max_out, 2)),
                        "sample_rate": default_samplerate,
                        "score": score,
                    }
                )

        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked

    @classmethod
    def get_microphone_candidates(cls) -> list[dict]:
        devices = cls.get_all_devices()
        ranked: list[dict] = []
        default_in_id, _default_out_id = cls.get_default_device_ids()

        for idx, device in enumerate(devices):
            name = str(device.get("name", ""))
            name_l = name.lower()
            max_in = int(device.get("max_input_channels", 0))
            if max_in <= 0:
                continue

            # Avoid loopback-like devices when explicitly selecting microphone capture
            if any(keyword in name_l for keyword in ("stereo mix", "loopback", "what u hear", "wave out mix")):
                continue

            default_samplerate = int(round(float(device.get("default_samplerate", 16000) or 16000)))
            score = 50
            if "microphone" in name_l or "mic" in name_l:
                score += 15
            if "headset" in name_l or "usb" in name_l:
                score += 8
            if "array" in name_l:
                score += 3
            if "mapper" in name_l or "primary sound" in name_l:
                score -= 10
            if default_in_id is not None and idx == default_in_id:
                score += 25

            ranked.append(
                {
                    "device_id": idx,
                    "name": name,
                    "use_wasapi_loopback": False,
                    "channels": max(1, min(max_in, 2)),
                    "sample_rate": default_samplerate,
                    "score": score,
                }
            )

        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked

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
        # Preferred backend: soundcard (works well for loopback capture).
        try:
            soundcard = self._load_soundcard()
            blocksize = int(self.capture_sample_rate * (chunk_duration_ms / 1000))
            self._stopped.clear()

            selected_name: str | None = None
            if self.device_id is not None:
                devices = self.get_all_devices()
                if 0 <= int(self.device_id) < len(devices):
                    selected_name = str(devices[int(self.device_id)].get("name", ""))

            if self.use_wasapi_loopback:
                speaker_name = selected_name
                if not speaker_name:
                    speaker = soundcard.default_speaker()
                    if speaker is None:
                        raise RuntimeError("No default speaker found for loopback capture")
                    speaker_name = str(speaker.name)

                mic = soundcard.get_microphone(id=speaker_name, include_loopback=True)
                if mic is None:
                    # If selected speaker is unsupported by soundcard loopback, fallback to default speaker.
                    speaker = soundcard.default_speaker()
                    if speaker is None:
                        raise RuntimeError("Unable to initialize loopback capture device")
                    speaker_name = str(speaker.name)
                    mic = soundcard.get_microphone(id=speaker_name, include_loopback=True)
                device_name = speaker_name
                requested_channels = 2
            else:
                if selected_name:
                    mic = soundcard.get_microphone(id=selected_name, include_loopback=False)
                else:
                    mic = soundcard.default_microphone()

                if mic is None:
                    mic = soundcard.default_microphone()
                if mic is None:
                    raise RuntimeError("No default microphone found")
                device_name = str(getattr(mic, "name", selected_name or "default microphone"))
                requested_channels = max(1, self.channels)

            if mic is None:
                raise RuntimeError("Unable to initialize soundcard capture device")

            print(f"[DEBUG] Starting audio stream from {device_name} via soundcard.")
            with mic.recorder(
                samplerate=self.capture_sample_rate,
                channels=requested_channels,
                blocksize=blocksize,
            ) as recorder:
                self._stream = recorder
                chunk_get_count = 0
                while True:
                    if self._stopped.is_set():
                        break
                    if stop_event is not None and stop_event.is_set():
                        break

                    data = recorder.record(numframes=blocksize)
                    if data is None:
                        continue

                    audio = np.asarray(data, dtype=np.float32)
                    if audio.ndim == 1:
                        audio = audio.reshape(-1, 1)
                    pcm = np.clip(audio, -1.0, 1.0)
                    chunk = (pcm * 32767.0).astype(np.int16).tobytes()

                    # Ensure recognizer-compatible format (mono + target sample rate).
                    normalized_chunk = self._normalize_audio_chunk(chunk)
                    chunk_get_count += 1
                    if chunk_get_count % 10 == 0:
                        print(f"[DEBUG] Got chunk #{chunk_get_count} from soundcard, size: {len(normalized_chunk)} bytes")
                    callback(normalized_chunk)

                self._stream = None
                return
        except Exception as soundcard_exc:
            print(f"[DEBUG] soundcard capture failed, falling back to sounddevice: {soundcard_exc}")

        sounddevice = self._load_sounddevice()

        blocksize = int(self.capture_sample_rate * (chunk_duration_ms / 1000))
        audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=50)
        self._stopped.clear()
        
        callback_count = [0]  # Use list to allow modification in nested function

        def _on_audio(indata, _frames, _time, status) -> None:
            callback_count[0] += 1
            normalized_chunk = self._normalize_audio_chunk(bytes(indata))
            if callback_count[0] % 5 == 0:  # Log every 5th callback
                print(
                    f"[DEBUG] Audio callback #{callback_count[0]} triggered, "
                    f"raw_bytes: {len(bytes(indata))}, normalized_bytes: {len(normalized_chunk)}, status: {status}"
                )
            
            if status:
                print(f"[DEBUG] Audio input status: {status}")
            try:
                audio_queue.put_nowait(normalized_chunk)
            except queue.Full:
                _ = audio_queue.get_nowait()
                audio_queue.put_nowait(normalized_chunk)

        device_name = "audio input"
        if self.device_id is not None:
            devices = sounddevice.query_devices()
            if isinstance(devices, list) and 0 <= self.device_id < len(devices):
                device_name = str(devices[self.device_id].get("name", "audio input"))

        print(f"[DEBUG] Starting audio stream from {device_name}. Press Ctrl+C to stop.")
        print(
            f"[DEBUG] Blocksize: {blocksize}, Capture sample rate: {self.capture_sample_rate}, "
            f"Target sample rate: {self.sample_rate}, Channels: {self.channels}, Device ID: {self.device_id}"
        )
        stream_kwargs = {
            "samplerate": self.capture_sample_rate,
            "blocksize": blocksize,
            "dtype": "int16",
            "channels": self.channels,
            "callback": _on_audio,
            "device": self.device_id,
        }
        if self.use_wasapi_loopback:
            try:
                # sounddevice API differs across versions; loopback kwarg is not always available.
                wasapi_settings = getattr(sounddevice, "WasapiSettings", None)
                if wasapi_settings is None:
                    raise RuntimeError("sounddevice.WasapiSettings is unavailable in this environment")

                parameters = inspect.signature(wasapi_settings).parameters
                if "loopback" in parameters:
                    stream_kwargs["extra_settings"] = wasapi_settings(loopback=True)
                else:
                    stream_kwargs["extra_settings"] = wasapi_settings()
            except Exception as exc:
                raise RuntimeError(f"Failed to enable WASAPI loopback: {exc}") from exc

        with sounddevice.RawInputStream(**stream_kwargs) as stream:
            self._stream = stream
            print(f"[DEBUG] Audio stream created and started")
            chunk_get_count = 0
            try:
                while True:
                    if self._stopped.is_set():
                        print(f"[DEBUG] Stop event set, breaking")
                        break
                    if stop_event is not None and stop_event.is_set():
                        print(f"[DEBUG] External stop event set, breaking")
                        break
                    try:
                        chunk = audio_queue.get(timeout=0.2)
                        chunk_get_count += 1
                        if chunk_get_count % 10 == 0:
                            print(f"[DEBUG] Got chunk #{chunk_get_count} from queue, size: {len(chunk)} bytes")
                        callback(chunk)
                    except queue.Empty:
                        if chunk_get_count % 50 == 0 and chunk_get_count > 0:
                            print(f"[DEBUG] Queue still empty (got {chunk_get_count} chunks so far)")
                        continue
            except KeyboardInterrupt:
                print("[DEBUG] Realtime stream stopped by KeyboardInterrupt.")
            except Exception as e:
                print(f"[DEBUG] Exception in stream_chunks: {e}")
                import traceback
                traceback.print_exc()
            finally:
                print(f"[DEBUG] Closing audio stream (got {chunk_get_count} chunks total)")
                self._stream = None
