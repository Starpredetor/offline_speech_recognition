from __future__ import annotations

from pathlib import Path
from typing import Optional, Callable

from config import AppConfig, is_vosk_model_dir
from core.audio import AudioInputHandler
from core.stt import RealtimeSTTEngine, FileTranscriber
from core.translation import TranslationEngine, LanguageDetector
from core.timestamp import TimestampGenerator
from core.utils import ensure_directory, print_section
from core.window import WindowTracker, WindowInfo
from ui import OverlayManager, OverlayConfig


class TranscriptionController:

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.realtime_stt = RealtimeSTTEngine(config=config)
        self.file_transcriber = FileTranscriber(config=config)
        self.translator = TranslationEngine(config=config)
        self.timestamp_generator = TimestampGenerator()
        self.language_detector = LanguageDetector()
        self.window_tracker = WindowTracker()
        self.overlay_manager = OverlayManager(
            config=OverlayConfig(font_size=16, font_family="Arial")
        )

    def setup_directories(self) -> None:
        ensure_directory(self.config.whisper_models_dir)
        ensure_directory(self.config.vosk_model_en.parent)
        ensure_directory(self.config.argos_models_dir)

    def get_available_languages(self) -> list[str]:
        langs: list[str] = []
        if is_vosk_model_dir(self.realtime_stt.config.vosk_model_en):
            langs.append("en")
        if is_vosk_model_dir(self.realtime_stt.config.vosk_model_hi):
            langs.append("hi")
        return langs

    @staticmethod
    def _resolve_target(detected_lang: str, selected_target: str | None) -> str | None:
        if selected_target is None:
            return None
        if selected_target == "other":
            if detected_lang == "en":
                return "hi"
            if detected_lang == "hi":
                return "en"
            return None
        return selected_target

    def run_realtime(
        self,
        src_lang: str = "en",
        tgt_lang: str | None = None,
        target_window: Optional[WindowInfo] = None,
    ) -> None:
        print_section("Real-Time Transcription Mode")
        source = src_lang if src_lang in {"en", "hi", "auto"} else "auto"
        target = (tgt_lang or "").strip().lower() or None

        available_langs = self.get_available_languages()

        if source in {"en", "hi"} and source not in available_langs:
            raise FileNotFoundError(
                f"Requested source language model '{source}' is missing. "
                f"Available extracted models: {available_langs or 'none'}."
            )

        if source == "auto" and not available_langs:
            raise FileNotFoundError(
                "No extracted Vosk model directories found. Extract model zips in models/vosk first."
            )

        if target == "auto":
            target = "other"

        if target not in {None, "en", "hi", "other"}:
            raise ValueError("Target language must be one of: en, hi, other")

        print("Listening from microphone.")
        print("Source language mode:", source)
        print("Target language mode:", target or "none")
        self.realtime_stt.reset_recognizers()
        if target_window:
            print(f"Target window: {target_window}")
            self.overlay_manager.initialize()
        print("Press Ctrl+C to stop.")

        audio_input = AudioInputHandler(
            sample_rate=self.realtime_stt.config.sample_rate,
            channels=self.realtime_stt.config.channels,
        )

        def _process_chunk(chunk: bytes) -> None:
            if source == "auto":
                en_final, en_text = (False, "")
                hi_final, hi_text = (False, "")

                if "en" in available_langs:
                    en_final, en_text = self.realtime_stt.accept_audio_chunk(chunk, lang="en")
                if "hi" in available_langs:
                    hi_final, hi_text = self.realtime_stt.accept_audio_chunk(chunk, lang="hi")

                if not en_final and not hi_final:
                    return

                text, detected_lang = self.language_detector.choose_best_candidate(
                    [
                        ("en", en_text if en_final else ""),
                        ("hi", hi_text if hi_final else ""),
                    ],
                    fallback="en",
                )
            else:
                is_final, text = self.realtime_stt.accept_audio_chunk(chunk, lang=source)
                if not is_final or not text:
                    return
                detected_lang = self.language_detector.detect_language(
                    text,
                    allowed=["en", "hi"],
                    fallback=source,
                )

            if not text:
                return

            print(f"\n[STT:{detected_lang}] {text}")
            self.overlay_manager.set_subtitle(f"[{detected_lang}] {text}")

            target_lang = self._resolve_target(detected_lang=detected_lang, selected_target=target)
            if target_lang is None or target_lang == detected_lang:
                return

            try:
                translated = self.translator.translate(text, from_lang=detected_lang, to_lang=target_lang)
            except RuntimeError as exc:
                print(f"[Translate Warning] {exc}")
                return

            print(f"[TR:{detected_lang}->{target_lang}] {translated}")
            self.overlay_manager.set_subtitle(translated)

        audio_input.stream_chunks(callback=_process_chunk)

    def run_file(
        self,
        audio_path: Path,
        src_lang: str = "en",
        tgt_lang: str | None = None,
    ) -> None:
        print_section("File Transcription Mode")
        segments = list(self.file_transcriber.transcribe(audio_path=audio_path, language=src_lang))
        lines = self.timestamp_generator.to_lines(segments)

        print_section("Transcript")
        for line in lines:
            print(line.render())

        if tgt_lang:
            print_section("Translated Transcript")
            for line in lines:
                translated_text = self.translator.translate(
                    line.text,
                    from_lang=src_lang,
                    to_lang=tgt_lang,
                )
                print(f"[{line.render().split(']')[0][1:]}] {translated_text}")
