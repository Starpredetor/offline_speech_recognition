from __future__ import annotations

import threading
from typing import Any

from flask import Flask, jsonify, render_template, request

from config import CONFIG, is_vosk_model_dir
from modules.argos_setup import setup_argos_models
from modules.audio_input import AudioInputHandler
from modules.file_transcriber import FileTranscriber
from modules.language_detector import LanguageDetector
from modules.realtime_stt import RealtimeSTTEngine
from modules.timestamp import TimestampGenerator
from modules.translator import TranslationEngine


class RealtimeWebSession:
    def __init__(self) -> None:
        self.realtime_stt = RealtimeSTTEngine(config=CONFIG)
        self.file_transcriber = FileTranscriber(config=CONFIG)
        self.translator = TranslationEngine(config=CONFIG)
        self.timestamp_generator = TimestampGenerator()
        self.language_detector = LanguageDetector()

        self._thread: threading.Thread | None = None
        self._audio_input: AudioInputHandler | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self._running = False
        self._messages: list[dict[str, Any]] = []
        self._next_id = 1

    def _add_message(self, kind: str, text: str) -> None:
        with self._lock:
            self._messages.append({"id": self._next_id, "kind": kind, "text": text})
            self._next_id += 1

    def _available_languages(self) -> list[str]:
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

    def start(self, src_lang: str, tgt_lang: str | None) -> tuple[bool, str]:
        with self._lock:
            if self._running:
                return False, "Realtime session is already running."

        has_mic, mic_message = AudioInputHandler.has_input_device()
        if not has_mic:
            return False, mic_message

        source = src_lang if src_lang in {"auto", "en", "hi"} else "auto"
        target = tgt_lang if tgt_lang in {None, "en", "hi", "other"} else None

        available = self._available_languages()
        if source in {"en", "hi"} and source not in available:
            return False, f"Missing extracted Vosk model for '{source}'. Available: {available or 'none'}."
        if source == "auto" and not available:
            return False, "No extracted Vosk model folders found in models/vosk."

        self._stop_event.clear()
        with self._lock:
            self._running = True

        self._thread = threading.Thread(
            target=self._run_stream,
            args=(source, target, available),
            daemon=True,
        )
        self._thread.start()
        return True, f"Realtime session started. Using input device: {mic_message}"

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            if not self._running:
                return False, "Realtime session is not running."
            thread = self._thread
            audio_input = self._audio_input
        self._stop_event.set()
        if audio_input is not None:
            audio_input.stop()
        if thread is not None:
            thread.join(timeout=2.0)
        return True, "Realtime session stopped."

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"running": self._running}

    def get_messages(self, since_id: int) -> list[dict[str, Any]]:
        with self._lock:
            return [msg for msg in self._messages if int(msg["id"]) > since_id]

    def _run_stream(self, source: str, target: str | None, available_langs: list[str]) -> None:
        audio_input = AudioInputHandler(
            sample_rate=self.realtime_stt.config.sample_rate,
            channels=self.realtime_stt.config.channels,
        )
        with self._lock:
            self._audio_input = audio_input

        self._add_message("status", f"Listening started. Source: {source}, Target: {target or 'none'}")

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
                    [("en", en_text if en_final else ""), ("hi", hi_text if hi_final else "")],
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

            self._add_message("stt", f"[{detected_lang}] {text}")

            target_lang = self._resolve_target(detected_lang=detected_lang, selected_target=target)
            if target_lang is None or target_lang == detected_lang:
                return

            try:
                translated = self.translator.translate(text, from_lang=detected_lang, to_lang=target_lang)
            except RuntimeError as exc:
                self._add_message("warning", f"Translation unavailable: {exc}")
                return

            self._add_message("translation", f"[{detected_lang} -> {target_lang}] {translated}")

        try:
            audio_input.stream_chunks(
                callback=_process_chunk,
                stop_event=self._stop_event,
            )
        except Exception as exc:
            self._add_message("error", str(exc))
        finally:
            with self._lock:
                self._running = False
                self._audio_input = None
            self._add_message("status", "Listening stopped.")


app = Flask(__name__)
session = RealtimeWebSession()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/realtime/status")
def realtime_status():
    return jsonify(session.status())


@app.get("/api/mic/check")
def mic_check():
    ok, message = AudioInputHandler.has_input_device()
    return jsonify({"ok": ok, "message": message})


@app.post("/api/argos/setup")
def argos_setup():
    ok, message = setup_argos_models()
    return jsonify({"ok": ok, "message": message})


@app.get("/api/realtime/messages")
def realtime_messages():
    since = request.args.get("since", default="0")
    try:
        since_id = int(since)
    except ValueError:
        since_id = 0
    return jsonify({"messages": session.get_messages(since_id=since_id)})


@app.post("/api/realtime/start")
def realtime_start():
    payload = request.get_json(silent=True) or {}
    source = str(payload.get("source", "auto")).strip().lower() or "auto"
    target = str(payload.get("target", "")).strip().lower() or None
    ok, message = session.start(src_lang=source, tgt_lang=target)
    return jsonify({"ok": ok, "message": message, **session.status()})


@app.post("/api/realtime/stop")
def realtime_stop():
    ok, message = session.stop()
    return jsonify({"ok": ok, "message": message, **session.status()})


if __name__ == "__main__":
    app.run(debug=True)
