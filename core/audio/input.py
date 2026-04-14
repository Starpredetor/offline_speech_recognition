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
        enable_voice_focus: bool = False,
    ) -> None:
        self.sample_rate = sample_rate
        self.capture_sample_rate = capture_sample_rate or sample_rate
        self.channels = channels
        self.device_id = device_id
        self.use_wasapi_loopback = use_wasapi_loopback
        self.enable_voice_focus = enable_voice_focus
        self._stream: Any | None = None
        self._stopped = threading.Event()
        self._hp_prev_sample = 0.0
        self._noise_floor = 120.0

    def _enhance_mic_voice(self, mono: np.ndarray) -> np.ndarray:
        if mono.size == 0:
            return mono

        mono_f = mono.astype(np.float32)
        emphasized = np.empty_like(mono_f)
        prev = self._hp_prev_sample
        coeff = 0.95
        for i in range(mono_f.size):
            current = mono_f[i]
            emphasized[i] = current - coeff * prev
            prev = current
        self._hp_prev_sample = float(prev)

        rms = float(np.sqrt(np.mean(np.square(emphasized), dtype=np.float64))) if emphasized.size else 0.0

        if rms < self._noise_floor * 1.35:
            self._noise_floor = 0.985 * self._noise_floor + 0.015 * max(1.0, rms)
        else:
            self._noise_floor = 0.998 * self._noise_floor + 0.002 * max(1.0, rms)

        gate_threshold = max(70.0, self._noise_floor * 1.4)
        if rms < gate_threshold:
            gain = 0.18
        elif rms < gate_threshold * 1.3:
            gain = 0.45
        elif rms < gate_threshold * 1.9:
            gain = 0.75
        else:
            gain = 1.0

        enhanced = emphasized * gain
        return np.clip(enhanced, -32768.0, 32767.0).astype(np.int16)

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

        if self.enable_voice_focus and not self.use_wasapi_loopback:
            mono = self._enhance_mic_voice(mono)

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
        chunk_duration_ms: int = 120,
        stop_event: threading.Event | None = None,
    ) -> None:
        blocksize = int(self.capture_sample_rate * (chunk_duration_ms / 1000))
        self._stopped.clear()

        try:
            sounddevice = self._load_sounddevice()
            audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=8)

            def _on_audio(indata, _frames, _time, status) -> None:
                normalized_chunk = self._normalize_audio_chunk(bytes(indata))
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
                chunk_get_count = 0
                try:
                    while True:
                        if self._stopped.is_set():
                            break
                        if stop_event is not None and stop_event.is_set():
                            break
                        try:
                            chunk = audio_queue.get(timeout=0.2)
                            chunk_get_count += 1
                            callback(chunk)
                        except queue.Empty:
                            continue
                finally:
                    self._stream = None
            return
        except Exception as sounddevice_exc:
            _ = sounddevice_exc

        try:
            soundcard = self._load_soundcard()

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

                    normalized_chunk = self._normalize_audio_chunk(chunk)
                    chunk_get_count += 1
                    callback(normalized_chunk)

                self._stream = None
                return
        except Exception as soundcard_exc:
            raise RuntimeError(
                "Audio capture failed for both sounddevice and soundcard backends. "
                f"sounddevice error: {sounddevice_exc}; soundcard error: {soundcard_exc}"
            ) from soundcard_exc
