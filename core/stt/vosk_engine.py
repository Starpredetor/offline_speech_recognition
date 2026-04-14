from __future__ import annotations

import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from typing import Any

from config import AppConfig, is_vosk_model_dir


class RealtimeSTTEngine:

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._models: dict[str, Any] = {}
        self._recognizers: dict[str, Any] = {}
        self._model_lock = RLock()
        self._recognizer_lock = RLock()
        self._model_cls: Any | None = None
        self._recognizer_cls: Any | None = None

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

    def _ensure_vosk_classes(self) -> tuple[Any, Any]:
        if self._model_cls is not None and self._recognizer_cls is not None:
            return self._model_cls, self._recognizer_cls

        model_cls, recognizer_cls = self._get_vosk_classes()
        self._model_cls = model_cls
        self._recognizer_cls = recognizer_cls
        return model_cls, recognizer_cls

    def _resolve_model_path(self, lang: str) -> Path:
        if lang == "hi":
            return self.config.vosk_model_hi
        return self.config.vosk_model_en

    def _get_model(self, lang: str) -> Any:
        with self._model_lock:
            model = self._models.get(lang)
            if model is not None:
                return model

        model_cls, _recognizer_cls = self._ensure_vosk_classes()
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
        with self._model_lock:
            existing = self._models.get(lang)
            if existing is not None:
                return existing
            self._models[lang] = model
        return model

    def _get_recognizer(self, lang: str) -> Any:
        with self._recognizer_lock:
            recognizer = self._recognizers.get(lang)
            if recognizer is not None:
                return recognizer

        _model_cls, recognizer_cls = self._ensure_vosk_classes()
        model = self._get_model(lang)
        recognizer = recognizer_cls(model, self.config.sample_rate)

        with self._recognizer_lock:
            existing = self._recognizers.get(lang)
            if existing is not None:
                return existing
            self._recognizers[lang] = recognizer
        return recognizer

    def prepare_languages(self, languages: list[str]) -> None:
        supported = [lang for lang in languages if lang in {"en", "hi"}]
        if not supported:
            return

        if len(supported) == 1:
            self._get_recognizer(supported[0])
            return

        with ThreadPoolExecutor(max_workers=len(supported)) as executor:
            futures = [executor.submit(self._get_recognizer, lang) for lang in supported]
            for future in futures:
                future.result()

    def is_language_ready(self, lang: str) -> bool:
        return lang in self._recognizers

    def reset_recognizers(self, lang: str | None = None) -> None:
        def _reset(recognizer: Any) -> None:
            reset_fn = getattr(recognizer, "Reset", None)
            if callable(reset_fn):
                reset_fn()

        with self._recognizer_lock:
            if lang is None:
                for recognizer in self._recognizers.values():
                    _reset(recognizer)
                return

            recognizer = self._recognizers.get(lang)
            if recognizer is not None:
                _reset(recognizer)

    def clear_recognizer_cache(self, lang: str | None = None) -> None:
        with self._recognizer_lock:
            if lang is None:
                self._recognizers.clear()
                return
            self._recognizers.pop(lang, None)

    def accept_audio_chunk(self, audio_chunk: bytes, lang: str = "en") -> tuple[bool, str]:
        state, text = self.accept_audio_chunk_detailed(audio_chunk=audio_chunk, lang=lang)
        return state == "final", text

    def accept_audio_chunk_detailed(self, audio_chunk: bytes, lang: str = "en") -> tuple[str, str]:
        recognizer = self._get_recognizer(lang)

        if recognizer.AcceptWaveform(audio_chunk):
            result = json.loads(recognizer.Result())
            text = result.get("text", "").strip()
            return ("final" if text else "empty"), text

        partial = json.loads(recognizer.PartialResult())
        text = partial.get("partial", "").strip()
        return ("partial" if text else "empty"), text

    def transcribe_chunk(self, audio_chunk: bytes, lang: str = "en") -> str:
        _is_final, text = self.accept_audio_chunk(audio_chunk=audio_chunk, lang=lang)
        return text
