from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
from typing import Any, Iterable

from config import AppConfig


@dataclass(slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


class FileTranscriber:
    """Faster-Whisper transcription engine for audio files."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._model: Any | None = None

    def _get_model(self) -> Any:
        try:
            faster_whisper = importlib.import_module("faster_whisper")
        except ModuleNotFoundError:
            raise RuntimeError(
                "faster-whisper is not installed. Install dependencies from requirements.txt"
            )

        model_cls = getattr(faster_whisper, "WhisperModel", None)
        if model_cls is None:
            raise RuntimeError("faster_whisper package is installed but WhisperModel was not found")

        if self._model is None:
            self._model = model_cls(
                self.config.whisper_model_size,
                device=self.config.whisper_device,
                compute_type=self.config.whisper_compute_type,
                download_root=str(self.config.whisper_models_dir),
            )
        return self._model

    def transcribe(self, audio_path: Path, language: str = "en") -> Iterable[TranscriptSegment]:
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        model = self._get_model()
        segments, _info = model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=True,
        )

        output: list[TranscriptSegment] = []
        for segment in segments:
            output.append(
                TranscriptSegment(start=float(segment.start), end=float(segment.end), text=segment.text.strip())
            )
        return output
