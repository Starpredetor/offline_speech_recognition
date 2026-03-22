from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from config import AppConfig, is_vosk_model_dir


class RealtimeSTTEngine:
    """Vosk-based real-time recognizer skeleton."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._models: dict[str, Any] = {}
        self._recognizers: dict[str, Any] = {}

    @staticmethod
    def _get_vosk_classes() -> tuple[Any, Any]:
        try:
            vosk = importlib.import_module("vosk")
        except ModuleNotFoundError:
            raise RuntimeError("vosk is not installed. Install dependencies from requirements.txt")

        model_cls = getattr(vosk, "Model", None)
        recognizer_cls = getattr(vosk, "KaldiRecognizer", None)
        if model_cls is None or recognizer_cls is None:
            raise RuntimeError("vosk package is installed but required classes were not found")
        return model_cls, recognizer_cls

    def _resolve_model_path(self, lang: str) -> Path:
        if lang == "hi":
            return self.config.vosk_model_hi
        return self.config.vosk_model_en

    def _get_model(self, lang: str) -> Any:
        model = self._models.get(lang)
        if model is not None:
            return model

        model_cls, _recognizer_cls = self._get_vosk_classes()
        model_path = self._resolve_model_path(lang)
        if not model_path.exists():
            zip_hint = self.config.vosk_model_hi_zip if lang == "hi" else self.config.vosk_model_en_zip
            extra_hint = ""
            if zip_hint.exists():
                extra_hint = f" Found zip at {zip_hint}; extract it inside models/vosk first."
            raise FileNotFoundError(
                f"Vosk model not found at: {model_path}.{extra_hint}"
            )

        if not is_vosk_model_dir(model_path):
            nested_hint = ""
            nested_candidate = model_path / model_path.name
            if is_vosk_model_dir(nested_candidate):
                nested_hint = (
                    f" Detected valid nested model at {nested_candidate}. "
                    "Update config to this folder or restart after config refresh."
                )
            raise FileNotFoundError(
                "Vosk model directory is invalid or incomplete at "
                f"{model_path}. Expected files: am/final.mdl and conf/.{nested_hint}"
            )

        try:
            model = model_cls(str(model_path))
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load Vosk model from {model_path}: {exc}. "
                "Verify the model is fully extracted and not nested twice."
            )
        self._models[lang] = model
        return model

    def _get_recognizer(self, lang: str) -> Any:
        recognizer = self._recognizers.get(lang)
        if recognizer is not None:
            return recognizer

        _model_cls, recognizer_cls = self._get_vosk_classes()
        model = self._get_model(lang)
        recognizer = recognizer_cls(model, self.config.sample_rate)
        self._recognizers[lang] = recognizer
        return recognizer

    def accept_audio_chunk(self, audio_chunk: bytes, lang: str = "en") -> tuple[bool, str]:
        recognizer = self._get_recognizer(lang)

        if recognizer.AcceptWaveform(audio_chunk):
            result = json.loads(recognizer.Result())
            return True, result.get("text", "").strip()

        partial = json.loads(recognizer.PartialResult())
        return False, partial.get("partial", "").strip()

    def transcribe_chunk(self, audio_chunk: bytes, lang: str = "en") -> str:
        _is_final, text = self.accept_audio_chunk(audio_chunk=audio_chunk, lang=lang)
        return text

    def stream_live(self, lang: str = "en") -> None:
        print("Live transcription entrypoint created. Connect AudioInputHandler stream here.")
