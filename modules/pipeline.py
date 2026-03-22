from __future__ import annotations

from pathlib import Path

from modules.audio_input import AudioInputHandler
from modules.file_transcriber import FileTranscriber
from modules.language_detector import LanguageDetector
from modules.realtime_stt import RealtimeSTTEngine
from modules.timestamp import TimestampGenerator
from modules.translator import TranslationEngine
from modules.utils import print_section


class PipelineController:
    """Orchestrates STT, timestamp formatting, and translation stages."""

    def __init__(
        self,
        realtime_stt: RealtimeSTTEngine,
        file_transcriber: FileTranscriber,
        translator: TranslationEngine,
        timestamp_generator: TimestampGenerator,
    ) -> None:
        self.realtime_stt = realtime_stt
        self.file_transcriber = file_transcriber
        self.translator = translator
        self.timestamp_generator = timestamp_generator
        self.language_detector = LanguageDetector()

    def run_realtime(self, src_lang: str = "en", tgt_lang: str | None = None) -> None:
        print_section("Realtime Mode")
        source = src_lang if src_lang in {"en", "hi", "auto"} else "auto"
        target = (tgt_lang or "").strip().lower() or None

        available_langs = []
        if self.realtime_stt.config.vosk_model_en.exists():
            available_langs.append("en")
        if self.realtime_stt.config.vosk_model_hi.exists():
            available_langs.append("hi")

        if source in {"en", "hi"} and source not in available_langs:
            raise FileNotFoundError(
                f"Requested source language model '{source}' is missing. Available extracted models: {available_langs or 'none'}."
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
        print("Press Ctrl+C to stop.")

        audio_input = AudioInputHandler(
            sample_rate=self.realtime_stt.config.sample_rate,
            channels=self.realtime_stt.config.channels,
        )

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

            target_lang = _resolve_target(detected_lang=detected_lang, selected_target=target)
            if target_lang is None or target_lang == detected_lang:
                return

            try:
                translated = self.translator.translate(text, from_lang=detected_lang, to_lang=target_lang)
            except RuntimeError as exc:
                print(f"[Translate Warning] {exc}")
                return

            print(f"[TR:{detected_lang}->{target_lang}] {translated}")

        audio_input.stream_chunks(callback=_process_chunk)

    def run_file(self, audio_path: Path, src_lang: str = "en", tgt_lang: str | None = None) -> None:
        print_section("File Mode")
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
