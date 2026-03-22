from __future__ import annotations

from pathlib import Path

from config import CONFIG
from modules.file_transcriber import FileTranscriber
from modules.pipeline import PipelineController
from modules.realtime_stt import RealtimeSTTEngine
from modules.timestamp import TimestampGenerator
from modules.translator import TranslationEngine
from modules.utils import ensure_directory


def setup_directories() -> None:
    ensure_directory(CONFIG.whisper_models_dir)
    ensure_directory(CONFIG.vosk_model_en.parent)
    ensure_directory(CONFIG.argos_models_dir)


def show_menu() -> None:
    print("\nOffline Speech Recognition")
    print("1. Start live transcription (Vosk)")
    print("2. Transcribe audio file (Faster-Whisper)")
    print("3. Translate text")
    print("4. Exit")


def run() -> None:
    setup_directories()

    realtime = RealtimeSTTEngine(config=CONFIG)
    file_transcriber = FileTranscriber(config=CONFIG)
    translator = TranslationEngine(config=CONFIG)
    timestamp_generator = TimestampGenerator()

    pipeline = PipelineController(
        realtime_stt=realtime,
        file_transcriber=file_transcriber,
        translator=translator,
        timestamp_generator=timestamp_generator,
    )

    while True:
        show_menu()
        choice = input("Choose an option: ").strip()

        if choice == "1":
            src_lang = input("Source language (auto/en/hi): ").strip().lower() or "auto"
            tgt_lang = input("Translate to (optional: en/hi/other): ").strip().lower()
            pipeline.run_realtime(src_lang=src_lang, tgt_lang=tgt_lang or None)
        elif choice == "2":
            audio_path = Path(input("Audio file path: ").strip())
            src_lang = input("Source language code (example: en): ").strip().lower() or "en"
            tgt_lang = input("Translate to (optional, en/hi): ").strip().lower()
            pipeline.run_file(audio_path=audio_path, src_lang=src_lang, tgt_lang=tgt_lang or None)
        elif choice == "3":
            text = input("Enter text: ").strip()
            src_lang = input("From (en/hi): ").strip().lower() or "en"
            tgt_lang = input("To (en/hi): ").strip().lower() or "hi"
            print("\nTranslated:")
            print(translator.translate(text, from_lang=src_lang, to_lang=tgt_lang))
        elif choice == "4":
            print("Goodbye.")
            return
        else:
            print("Invalid option. Try again.")


if __name__ == "__main__":
    run()
