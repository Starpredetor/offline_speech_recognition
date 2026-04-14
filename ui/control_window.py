from __future__ import annotations

import threading
import time
import re
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional
import numpy as np

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QTextEdit, QFrame, QSystemTrayIcon, QMenu, QApplication,
    QProgressBar, QCheckBox, QSplitter, QToolButton, QSlider, QFileDialog, QMessageBox
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QRect
from PySide6.QtGui import QFont
from PySide6.QtCore import QCoreApplication

from config import CONFIG
from core import TranscriptionController, AudioInputHandler
from core.window import WindowInfo


class WindowAudioWorker(QThread):
    message_signal = Signal(str, str)
    subtitle_signal = Signal(str)
    clear_subtitle_signal = Signal()
    audio_level_signal = Signal(int)
    finished_signal = Signal()
    error_signal = Signal(str)

    def __init__(
        self,
        controller: TranscriptionController,
        src_lang: str,
        capture_mode: str,
        target_lang: str,
        target_window: Optional[WindowInfo],
        preferred_mic_device_id: Optional[int] = None,
        mic_voice_focus: bool = True,
    ):
        super().__init__()
        self.controller = controller
        self.src_lang = src_lang
        self.capture_mode = capture_mode
        self.target_lang = target_lang
        self.target_window = target_window
        self.preferred_mic_device_id = preferred_mic_device_id
        self.mic_voice_focus = mic_voice_focus
        self._stop_event = threading.Event()
        self._attempt_stop_event: Optional[threading.Event] = None

    def run(self):
        translation_executor: ThreadPoolExecutor | None = None
        if self.target_lang != "none":
            translation_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="caption-translate")

        try:
            if not self.target_window:
                self.error_signal.emit("No window selected for audio capture")
                return

            self.message_signal.emit("status", f"🎤 Capturing audio from: {self.target_window.title}")

            system_audio_candidates = AudioInputHandler.get_system_audio_candidates() if self.capture_mode == "system" else []
            mic_candidates = AudioInputHandler.get_microphone_candidates() if self.capture_mode == "mic" else []
            if self.capture_mode == "mic" and self.preferred_mic_device_id is not None and mic_candidates:
                selected = next(
                    (candidate for candidate in mic_candidates if candidate.get("device_id") == self.preferred_mic_device_id),
                    None,
                )
                if selected is not None:
                    mic_candidates = [selected]
                else:
                    self.message_signal.emit(
                        "warning",
                        "⚠ Selected microphone is unavailable. Falling back to best available microphone.",
                    )

            if self.capture_mode == "system":
                if system_audio_candidates:
                    best_candidate = system_audio_candidates[0]
                    source_label = "WASAPI loopback" if best_candidate.get("use_wasapi_loopback") else "Stereo Mix"
                    self.message_signal.emit(
                        "status",
                        f"🔊 Audio source: System audio ({source_label} - {best_candidate.get('name', 'device')})",
                    )
                else:
                    self.message_signal.emit(
                        "warning",
                        "⚠ No system audio capture device found. Falling back to microphone.",
                    )
                    self.message_signal.emit("status", "🎤 Audio source: Microphone")
            else:
                if mic_candidates:
                    self.message_signal.emit(
                        "status",
                        f"🎤 Audio source: Microphone ({mic_candidates[0].get('name', 'default')})",
                    )
                else:
                    self.message_signal.emit("status", "🎤 Audio source: Microphone")
            self.message_signal.emit("status", "⏳ Loading speech model...")
            preload_started = time.perf_counter()
            if self.src_lang == "auto":
                preload_langs = self.controller.get_available_languages()
            else:
                preload_langs = [self.src_lang]
            self.controller.realtime_stt.prepare_languages(preload_langs)
            preload_elapsed = time.perf_counter() - preload_started
            self.message_signal.emit("status", f"✅ Speech model ready ({preload_elapsed:.1f}s)")
            if self._stop_event.is_set():
                return
            chunk_count = 0
            last_preview = ""
            partial_count = 0
            final_count = 0
            non_silent_chunk_count = 0
            last_debug_report = time.monotonic()
            first_chunk_seen = False
            input_energy_threshold = 150.0
            active_device_name = "unknown"
            silent_chunk_count = 0
            silence_threshold_chunks = 10
            translation_cache: dict[tuple[str, str, str], str] = {}
            latest_translation_seq = 0
            latest_emitted_seq = 0
            overlay_max_chars = 56
            deferred_overlay_lines: list[str] = []
            last_final_text_by_lang: dict[str, str] = {}

            def split_overlay_lines(
                text: str,
                max_length: int = overlay_max_chars,
                max_words: int = 10,
            ) -> list[str]:
                compact = " ".join(text.split()).strip()
                if not compact:
                    return []

                sentence_parts = [part.strip() for part in re.split(r"(?<=[.!?।])\s+", compact) if part.strip()]
                if not sentence_parts:
                    sentence_parts = [compact]

                lines: list[str] = []
                for sentence in sentence_parts:
                    if len(sentence) <= max_length:
                        words = sentence.split()
                        if len(words) <= max_words:
                            lines.append(sentence)
                        else:
                            for i in range(0, len(words), max_words):
                                lines.append(" ".join(words[i : i + max_words]))
                        continue

                    words = sentence.split()
                    if not words:
                        continue

                    current = words[0]
                    for word in words[1:]:
                        candidate = f"{current} {word}"
                        if len(candidate) <= max_length:
                            current = candidate
                        else:
                            lines.append(current)
                            current = word
                    if current:
                        lines.append(current)

                return lines

            def extract_incremental_text(current: str, previous: str) -> str:
                cur = " ".join(current.split()).strip()
                prev = " ".join(previous.split()).strip()
                if not cur:
                    return ""
                if not prev:
                    return cur
                if cur == prev:
                    return ""
                if cur.startswith(prev):
                    return cur[len(prev):].strip(" ,.;:!?-\u0964")

                prev_words = prev.split()
                cur_words = cur.split()
                max_overlap = min(len(prev_words), len(cur_words))
                overlap = 0
                for size in range(max_overlap, 0, -1):
                    if prev_words[-size:] == cur_words[:size]:
                        overlap = size
                        break

                incremental_words = cur_words[overlap:]
                return " ".join(incremental_words).strip()

            def enqueue_overlay_text(text: str) -> None:
                lines = split_overlay_lines(text)
                if lines:
                    deferred_overlay_lines.extend(lines)

            def emit_next_overlay_line() -> None:
                if not deferred_overlay_lines:
                    return
                self.subtitle_signal.emit(deferred_overlay_lines.pop(0))

            def emit_transcript(text: str, detected_lang: str, is_final: bool) -> None:
                nonlocal last_preview, silent_chunk_count
                nonlocal partial_count, final_count
                nonlocal latest_translation_seq, latest_emitted_seq
                if not text.strip():
                    return

                if self.capture_mode == "mic" and not is_final:
                    return

                source_text = text

                if not is_final and source_text == last_preview:
                    return

                silent_chunk_count = 0  

                resolved_target = None
                should_translate_for_output = False
                if is_final and translation_executor is not None:
                    resolved_target = self.controller._resolve_target(detected_lang, self.target_lang)
                    should_translate_for_output = bool(resolved_target and resolved_target != detected_lang)

                if is_final:
                    final_count += 1
                    last_preview = ""
                    prior_text = last_final_text_by_lang.get(detected_lang, "")
                    incremental_text = extract_incremental_text(source_text, prior_text)
                    if not incremental_text:
                        return

                    last_final_text_by_lang[detected_lang] = source_text
                    if not should_translate_for_output:
                        enqueue_overlay_text(incremental_text)
                        emit_next_overlay_line()
                    for short_line in split_overlay_lines(incremental_text):
                        self.message_signal.emit("stt", f"[{detected_lang.upper()}] {short_line}")
                else:
                    partial_count += 1
                    last_preview = source_text
                    preview_text = f"{source_text} ..."
                    self.message_signal.emit("stt_partial", f"[{detected_lang.upper()}] {preview_text}")

                if translation_executor is None or not is_final or not should_translate_for_output or resolved_target is None:
                    return

                cache_key = (detected_lang, resolved_target, source_text)
                cached_translation = translation_cache.get(cache_key)
                if cached_translation is not None:
                    enqueue_overlay_text(cached_translation)
                    emit_next_overlay_line()
                    self.message_signal.emit("stt", f"[{resolved_target.upper()}] {cached_translation}")
                    return

                latest_translation_seq += 1
                request_seq = latest_translation_seq

                def _translate_job(seq: int, source: str, from_lang: str, to_lang: str):
                    try:
                        translated_text = self.controller.translator.translate(
                            source,
                            from_lang=from_lang,
                            to_lang=to_lang,
                        )
                        return (seq, source, from_lang, to_lang, translated_text, None)
                    except RuntimeError as exc:
                        return (seq, source, from_lang, to_lang, "", str(exc))

                translation_future = translation_executor.submit(
                    _translate_job,
                    request_seq,
                    source_text,
                    detected_lang,
                    resolved_target,
                )

                def _on_translation_done(fut: Future) -> None:
                    nonlocal latest_emitted_seq
                    if self._stop_event.is_set():
                        return

                    if fut.cancelled():
                        return

                    try:
                        seq, source, from_lang, to_lang, translated_text, error_message = fut.result()
                    except Exception as exc:
                        self.message_signal.emit("warning", f"⚠ Translation worker error: {exc}")
                        return

                    if seq < latest_translation_seq or seq <= latest_emitted_seq:
                        return

                    latest_emitted_seq = seq
                    if error_message:
                        self.message_signal.emit("warning", f"⚠ Translation unavailable: {error_message}")
                        return

                    translation_cache[(from_lang, to_lang, source)] = translated_text
                    enqueue_overlay_text(translated_text)
                    emit_next_overlay_line()
                    self.message_signal.emit("stt", f"[{to_lang.upper()}] {translated_text}")

                translation_future.add_done_callback(_on_translation_done)

            def process_chunk(chunk: bytes) -> None:
                nonlocal chunk_count, non_silent_chunk_count, last_debug_report, first_chunk_seen
                nonlocal silent_chunk_count
                chunk_count += 1
                first_chunk_seen = True

                pcm = np.frombuffer(chunk, dtype=np.int16)
                if pcm.size > 0:
                    avg_energy = float(np.mean(np.abs(pcm)))
                    level = int(max(0.0, min(100.0, avg_energy / 120.0)))
                    self.audio_level_signal.emit(level)
                    if avg_energy >= input_energy_threshold:
                        non_silent_chunk_count += 1
                        silent_chunk_count = 0
                    else:
                        silent_chunk_count += 1
                        if silent_chunk_count >= silence_threshold_chunks:
                            if chunk_count > 10:
                                deferred_overlay_lines.clear()
                                self.clear_subtitle_signal.emit()
                                silent_chunk_count = 0
                else:
                    avg_energy = 0.0
                    silent_chunk_count += 1

                now = time.monotonic()
                if now - last_debug_report >= 5.0:
                    total_outputs = partial_count + final_count
                    self.message_signal.emit(
                        "debug",
                        (
                            f"debug: device={active_device_name}, chunks={chunk_count}, non_silent_chunks={non_silent_chunk_count}, "
                            f"partial={partial_count}, final={final_count}, last_energy={avg_energy:.1f}"
                        ),
                    )
                    if chunk_count >= 25 and non_silent_chunk_count == 0:
                        if total_outputs > 0:
                            last_debug_report = now
                        else:
                            self.message_signal.emit(
                                "warning",
                                "⚠ Input stream is active but appears silent (no significant audio energy detected).",
                            )
                    if chunk_count >= 25 and total_outputs == 0 and non_silent_chunk_count > 0:
                        self.message_signal.emit(
                            "warning",
                            "⚠ Audio input detected but no transcription output yet. Check selected language/model.",
                        )
                    last_debug_report = now

                
                if self._stop_event.is_set():
                    if self._attempt_stop_event is not None:
                        self._attempt_stop_event.set()
                    return

                if self.src_lang == "auto":
                    en_state, en_text = ("empty", "")
                    hi_state, hi_text = ("empty", "")

                    available = self.controller.get_available_languages()
                    if "en" in available:
                        en_state, en_text = self.controller.realtime_stt.accept_audio_chunk_detailed(chunk, lang="en")
                    if "hi" in available:
                        hi_state, hi_text = self.controller.realtime_stt.accept_audio_chunk_detailed(chunk, lang="hi")

                    if en_state == "empty" and hi_state == "empty":
                        return

                    text, detected_lang = self.controller.language_detector.choose_best_candidate(
                        [("en", en_text), ("hi", hi_text)],
                        fallback="en",
                    )
                    is_final = "final" in {en_state, hi_state}
                else:
                    state, text = self.controller.realtime_stt.accept_audio_chunk_detailed(chunk, lang=self.src_lang)
                    if state == "empty" or not text:
                        if chunk_count % 50 == 0:
                        return
                    is_final = state == "final"
                    detected_lang = self.src_lang

                if not text.strip():
                    return

                emit_transcript(text=text, detected_lang=detected_lang, is_final=is_final)

            candidate_devices: list[dict]
            if self.capture_mode == "system" and system_audio_candidates:
                candidate_devices = system_audio_candidates
            elif self.capture_mode == "mic" and mic_candidates:
                candidate_devices = mic_candidates
            else:
                candidate_devices = [
                    {
                        "device_id": None,
                        "name": "Default microphone",
                        "use_wasapi_loopback": False,
                        "channels": self.controller.config.channels,
                        "sample_rate": self.controller.config.sample_rate,
                    }
                ]
            if candidate_devices:
                candidate_devices = [candidate_devices[0]]
            last_error: Exception | None = None

            for attempt_index, candidate in enumerate(candidate_devices, start=1):
                try:
                    device_id = candidate.get("device_id")
                    stream_channels = int(candidate.get("channels", self.controller.config.channels))
                    capture_sample_rate = int(candidate.get("sample_rate", self.controller.config.sample_rate))
                    use_wasapi_loopback = bool(candidate.get("use_wasapi_loopback", False))
                    device_name = str(candidate.get("name", "audio device"))
                    active_device_name = device_name
                    attempt_stop_event = threading.Event()
                    self._attempt_stop_event = attempt_stop_event
                    self.message_signal.emit(
                        "status",
                        f"🎧 Trying device {attempt_index}/{len(candidate_devices)}: {device_name}",
                    )
                    channel_options: list[int] = [stream_channels]
                    if use_wasapi_loopback:
                        channel_options.extend([2, 1])
                    else:
                        channel_options.append(1)
                    deduped_channel_options: list[int] = []
                    for channel_value in channel_options:
                        if channel_value > 0 and channel_value not in deduped_channel_options:
                            deduped_channel_options.append(channel_value)

                    open_error: Exception | None = None
                    stream_opened = False
                    for channel_value in deduped_channel_options:
                        try:
                            chunk_duration_ms = 100 if use_wasapi_loopback or self.capture_mode == "system" else 140
                            audio_input = AudioInputHandler(
                                sample_rate=self.controller.config.sample_rate,
                                channels=channel_value,
                                device_id=device_id,
                                capture_sample_rate=capture_sample_rate,
                                use_wasapi_loopback=use_wasapi_loopback,
                                enable_voice_focus=self.mic_voice_focus and self.capture_mode == "mic",
                            )
                            audio_input.stream_chunks(
                                callback=process_chunk,
                                chunk_duration_ms=chunk_duration_ms,
                                stop_event=attempt_stop_event,
                            )
                            stream_opened = True
                            open_error = None
                            break
                        except Exception as channel_exc:
                            open_error = channel_exc
                            if "Invalid number of channels" in str(channel_exc):
                                self.message_signal.emit(
                                    "warning",
                                    f"⚠ Device {device_name} rejected {channel_value} channel(s), retrying...",
                                )
                                continue
                            raise

                    if not stream_opened and open_error is not None:
                        raise open_error

                    self._attempt_stop_event = None
                    last_error = None
                    break
                except Exception as exc:
                    self._attempt_stop_event = None
                    last_error = exc
                    retryable_errors = (
                        "Invalid device",
                        "Invalid sample rate",
                        "Invalid number of channels",
                        "Unanticipated host error",
                        "Error opening",
                    )
                    if self.capture_mode != "system" or not any(error_text in str(exc) for error_text in retryable_errors):
                        raise
                    self.message_signal.emit(
                        "warning",
                        f"⚠ System audio device failed ({candidate.get('name', 'unknown')}), trying another...",
                    )

            if last_error is not None:
                raise last_error

            total_outputs = partial_count + final_count
            if not first_chunk_seen:
                self.message_signal.emit("warning", "⚠ No audio chunks received from selected input device.")
            elif total_outputs > 0 and chunk_count == 0:
                self.message_signal.emit("warning", "⚠ Output generated without input chunks (unexpected state).")

            self.message_signal.emit(
                "debug",
                (
                    f"debug summary: chunks={chunk_count}, non_silent_chunks={non_silent_chunk_count}, "
                    f"partial={partial_count}, final={final_count}"
                ),
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error_signal.emit(str(e))
        finally:
            if translation_executor is not None:
                translation_executor.shutdown(wait=False, cancel_futures=True)
            self.finished_signal.emit()

    def stop(self):
        self._stop_event.set()
        if self._attempt_stop_event is not None:
            self._attempt_stop_event.set()


class ModelPreloadWorker(QThread):
    finished_signal = Signal(float, list)
    error_signal = Signal(str)

    def __init__(self, controller: TranscriptionController, languages: list[str]):
        super().__init__()
        self.controller = controller
        self.languages = languages

    def run(self):
        try:
            started = time.perf_counter()
            self.controller.realtime_stt.prepare_languages(self.languages)
            elapsed = time.perf_counter() - started
            self.finished_signal.emit(elapsed, self.languages)
        except Exception as exc:
            self.error_signal.emit(str(exc))


class TranscriptionControlWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.controller = TranscriptionController(config=CONFIG)
        self.controller.setup_directories()

        self.worker_thread: Optional[WindowAudioWorker] = None
        self.preload_thread: Optional[ModelPreloadWorker] = None
        self.is_running = False
        self.is_stopping = False
        self.models_ready = False
        self.last_stt_event_ts: float | None = None
        self.overlay_enabled = True
        self.current_overlay_position = "Bottom"
        
        self.is_dark_mode = self.detect_dark_mode()

        self.setWindowTitle("Offline Caption Studio")
        self.setGeometry(90, 90, 1080, 760)
        self.setStyleSheet(self.get_stylesheet())

        self.init_ui()
        
        self.setup_tray()
        
        self.window_refresh_timer = QTimer()
        self.window_refresh_timer.timeout.connect(self.refresh_windows)
        self.window_refresh_timer.start(3000)
        
        self.refresh_windows()
        self.source_combo.currentTextChanged.connect(self.on_source_language_changed)
        self.capture_combo.currentTextChanged.connect(self.on_capture_mode_changed)
        self.source_combo.currentTextChanged.connect(self.update_selection_summary)
        self.target_combo.currentTextChanged.connect(self.update_selection_summary)
        self.refresh_microphone_devices()
        self.on_capture_mode_changed(self.capture_combo.currentText())
        self.on_translation_enabled_toggled(self.translation_enabled_checkbox.isChecked())
        self.update_selection_summary()

    def detect_dark_mode(self) -> bool:
        app = QApplication.instance()
        if app:
            palette = app.palette()
            bg_color = palette.color(palette.ColorRole.Window)
            return bg_color.lightness() < 128
        return False

    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        
        tray_menu = QMenu(self)
        
        restore_action = tray_menu.addAction("Restore")
        restore_action.triggered.connect(self.restore_from_tray)
        
        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_application)
        
        self.tray_icon.setContextMenu(tray_menu)
        
        try:
            self.tray_icon.setIcon(QApplication.style().standardIcon(
                QApplication.style().StandardPixmap.SP_MediaPlay
            ))
        except:
            pass
        
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.setToolTip("Offline Caption Studio - Ready")
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            if self.isVisible():
                self.hide()
            else:
                self.restore_from_tray()

    def restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_application(self):
        if self.is_running and self.worker_thread:
            self.worker_thread.stop()
            self.worker_thread.wait(2000)
        self.window_refresh_timer.stop()
        QCoreApplication.quit()
    def get_stylesheet(self) -> str:
        if self.is_dark_mode:
            return """
                QMainWindow {
                    background-color: #0f172a;
                }
                
                QLabel {
                    color: #e2e8f0;
                }

                QLabel#sectionHeader {
                    color: #cbd5e1;
                    font-size: 13px;
                    font-weight: 700;
                    letter-spacing: 0.4px;
                }
                
                QComboBox {
                    background-color: #111827;
                    border: 1px solid #334155;
                    border-radius: 10px;
                    padding: 9px 12px;
                    font-size: 14px;
                    font-family: "Bahnschrift";
                    color: #e2e8f0;
                    selection-background-color: #0369a1;
                }
                
                QComboBox:hover {
                    border: 1px solid #38bdf8;
                }
                
                QComboBox::drop-down {
                    border: none;
                }
                
                QComboBox QAbstractItemView {
                    background-color: #0b1220;
                    color: #e2e8f0;
                    selection-background-color: #0369a1;
                }

                QCheckBox {
                    color: #cbd5e1;
                    font-family: "Bahnschrift";
                    spacing: 8px;
                }

                QCheckBox::indicator {
                    width: 16px;
                    height: 16px;
                    border-radius: 4px;
                    border: 1px solid #475569;
                    background: #0b1220;
                }

                QCheckBox::indicator:checked {
                    background: #0ea5e9;
                    border: 1px solid #0ea5e9;
                }

                QPushButton {
                    background-color: #1e293b;
                    color: #e2e8f0;
                    border: 1px solid #334155;
                    border-radius: 10px;
                    padding: 9px 14px;
                    font-family: "Bahnschrift";
                    font-size: 12px;
                    font-weight: 600;
                }

                QPushButton:hover {
                    background-color: #334155;
                    border-color: #475569;
                }

                QPushButton#primaryButton {
                    background-color: #0ea5e9;
                    color: #001018;
                    border: 1px solid #38bdf8;
                    font-size: 13px;
                    font-weight: 700;
                }

                QPushButton#primaryButton:hover {
                    background-color: #38bdf8;
                }
                
                QTextEdit {
                    background-color: #0b1220;
                    border: 1px solid #334155;
                    border-radius: 12px;
                    padding: 12px;
                    font-family: "Consolas";
                    font-size: 12px;
                    color: #cbd5e1;
                }
                
                QStatusBar {
                    background-color: #0b1220;
                    border-top: 1px solid #1f2937;
                    color: #cbd5e1;
                }
                
                QFrame {
                    background-color: #111827;
                    border: 1px solid #1f2937;
                    border-radius: 14px;
                }

                QProgressBar {
                    border: 1px solid #334155;
                    border-radius: 5px;
                    background: #0b1220;
                }

                QProgressBar::chunk {
                    border-radius: 5px;
                    background-color: #0ea5e9;
                }
            """
        else:
            return """
                QMainWindow {
                    background-color: #eef2f6;
                }
                
                QLabel {
                    color: #0f172a;
                }

                QLabel#sectionHeader {
                    color: #334155;
                    font-size: 13px;
                    font-weight: 700;
                    letter-spacing: 0.4px;
                }
                
                QComboBox {
                    background-color: #ffffff;
                    border: 1px solid #cbd5e1;
                    border-radius: 10px;
                    padding: 9px 12px;
                    font-size: 14px;
                    font-family: "Bahnschrift";
                    color: #0f172a;
                    selection-background-color: #38bdf8;
                }
                
                QComboBox:hover {
                    border: 1px solid #0ea5e9;
                }
                
                QComboBox::drop-down {
                    border: none;
                }
                
                QComboBox QAbstractItemView {
                    background-color: #ffffff;
                    color: #0f172a;
                    selection-background-color: #7dd3fc;
                }

                QCheckBox {
                    color: #334155;
                    font-family: "Bahnschrift";
                    spacing: 8px;
                }

                QCheckBox::indicator {
                    width: 16px;
                    height: 16px;
                    border-radius: 4px;
                    border: 1px solid #94a3b8;
                    background: #ffffff;
                }

                QCheckBox::indicator:checked {
                    background: #0ea5e9;
                    border: 1px solid #0ea5e9;
                }

                QPushButton {
                    background-color: #f8fafc;
                    color: #0f172a;
                    border: 1px solid #cbd5e1;
                    border-radius: 10px;
                    padding: 9px 14px;
                    font-family: "Bahnschrift";
                    font-size: 12px;
                    font-weight: 600;
                }

                QPushButton:hover {
                    background-color: #e2e8f0;
                    border-color: #94a3b8;
                }

                QPushButton#primaryButton {
                    background-color: #0ea5e9;
                    color: #001018;
                    border: 1px solid #38bdf8;
                    font-size: 13px;
                    font-weight: 700;
                }

                QPushButton#primaryButton:hover {
                    background-color: #38bdf8;
                }
                
                QTextEdit {
                    background-color: #ffffff;
                    border: 1px solid #cbd5e1;
                    border-radius: 12px;
                    padding: 12px;
                    font-family: "Consolas";
                    font-size: 12px;
                    color: #0f172a;
                }
                
                QStatusBar {
                    background-color: #ffffff;
                    border-top: 1px solid #cbd5e1;
                    color: #334155;
                }
                
                QFrame {
                    background-color: #ffffff;
                    border: 1px solid #cbd5e1;
                    border-radius: 14px;
                }

                QProgressBar {
                    border: 1px solid #cbd5e1;
                    border-radius: 5px;
                    background: #f8fafc;
                }

                QProgressBar::chunk {
                    border-radius: 5px;
                    background-color: #0ea5e9;
                }
            """

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setSpacing(12)
        layout.setContentsMargins(14, 14, 14, 14)

        top_bar = QFrame()
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(12, 10, 12, 10)
        top_layout.setSpacing(10)

        title = QLabel("Offline Speech Recognition")
        title_font = QFont("Bahnschrift", 18)
        title_font.setBold(True)
        title.setFont(title_font)
        top_layout.addWidget(title)
        top_layout.addStretch()

        top_layout.addWidget(QLabel("Mode"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Real-time", "File transcription"])
        self.mode_combo.setMinimumWidth(170)
        self.mode_combo.currentTextChanged.connect(self.on_mode_changed)
        top_layout.addWidget(self.mode_combo)

        self.theme_toggle = QCheckBox("Dark Theme")
        self.theme_toggle.setChecked(self.is_dark_mode)
        self.theme_toggle.toggled.connect(self.on_theme_toggled)
        top_layout.addWidget(self.theme_toggle)

        self.settings_btn = QToolButton()
        self.settings_btn.setText("Settings")
        self.settings_btn.clicked.connect(self.open_settings_dialog)
        top_layout.addWidget(self.settings_btn)

        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("primaryButton")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.setMinimumWidth(140)
        self.start_btn.setFont(QFont("Bahnschrift", 12, QFont.Weight.Bold))
        self.start_btn.clicked.connect(self.on_start_clicked)
        self.start_btn.setEnabled(False)
        top_layout.addWidget(self.start_btn)

        layout.addWidget(top_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        self.settings_frame = QFrame()
        left_layout = QVBoxLayout(self.settings_frame)
        left_layout.setSpacing(10)
        left_layout.setContentsMargins(14, 14, 14, 14)

        audio_header = QLabel("Audio Settings")
        audio_header.setObjectName("sectionHeader")
        left_layout.addWidget(audio_header)

        capture_row = QHBoxLayout()
        capture_row.addWidget(QLabel("Audio Source:"))
        self.capture_combo = QComboBox()
        self.capture_combo.addItems(["Microphone", "System Audio", "Virtual Device"])
        self.capture_combo.setMinimumHeight(34)
        self.capture_combo.setFont(QFont("Bahnschrift", 11))
        capture_row.addWidget(self.capture_combo)
        left_layout.addLayout(capture_row)

        mic_row = QHBoxLayout()
        self.mic_label = QLabel("Input Device:")
        mic_row.addWidget(self.mic_label)
        self.mic_combo = QComboBox()
        self.mic_combo.setMinimumHeight(34)
        self.mic_combo.setFont(QFont("Bahnschrift", 11))
        mic_row.addWidget(self.mic_combo)
        self.refresh_mic_btn = QPushButton("Refresh")
        self.refresh_mic_btn.clicked.connect(self.refresh_microphone_devices)
        mic_row.addWidget(self.refresh_mic_btn)
        left_layout.addLayout(mic_row)

        self.voice_focus_checkbox = QCheckBox("Noise suppression / voice focus")
        self.voice_focus_checkbox.setChecked(True)
        left_layout.addWidget(self.voice_focus_checkbox)

        meter_row = QHBoxLayout()
        meter_row.addWidget(QLabel("Input Level:"))
        self.audio_level_bar = QProgressBar()
        self.audio_level_bar.setRange(0, 100)
        self.audio_level_bar.setValue(0)
        self.audio_level_bar.setTextVisible(False)
        meter_row.addWidget(self.audio_level_bar)
        left_layout.addLayout(meter_row)

        language_header = QLabel("Language Settings")
        language_header.setObjectName("sectionHeader")
        left_layout.addWidget(language_header)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Source:"))
        self.source_combo = QComboBox()
        self.source_combo.addItems(["Auto Detect", "English", "Hindi"])
        self.source_combo.setMinimumHeight(34)
        self.source_combo.setFont(QFont("Bahnschrift", 11))
        source_row.addWidget(self.source_combo)
        left_layout.addLayout(source_row)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Target:"))
        self.target_combo = QComboBox()
        self.target_combo.addItems(["None", "English", "Hindi", "Auto Opposite"])
        self.target_combo.setMinimumHeight(34)
        self.target_combo.setFont(QFont("Bahnschrift", 11))
        self.target_combo.currentTextChanged.connect(self.on_target_language_changed)
        target_row.addWidget(self.target_combo)
        left_layout.addLayout(target_row)

        self.selection_summary = QLabel("")
        self.selection_summary.setFont(QFont("Bahnschrift", 10, QFont.Weight.Bold))
        self.selection_summary.setStyleSheet("color: #0ea5e9;")
        left_layout.addWidget(self.selection_summary)

        model_header = QLabel("Model Settings")
        model_header.setObjectName("sectionHeader")
        left_layout.addWidget(model_header)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("STT Engine:"))
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["Vosk (fast)"])
        self.engine_combo.setEnabled(False)
        model_row.addWidget(self.engine_combo)
        left_layout.addLayout(model_row)

        whisper_row = QHBoxLayout()
        whisper_row.addWidget(QLabel("Whisper Model:"))
        self.whisper_model_combo = QComboBox()
        self.whisper_model_combo.addItems(["Unavailable (removed)"])
        self.whisper_model_combo.setEnabled(False)
        whisper_row.addWidget(self.whisper_model_combo)
        left_layout.addLayout(whisper_row)

        preload_row = QHBoxLayout()
        preload_row.addWidget(QLabel("Speech Models:"))
        self.model_status = QLabel("Loading...")
        self.model_status.setStyleSheet("color: #f59e0b; font-weight: bold;")
        preload_row.addWidget(self.model_status)
        self.preload_btn = QPushButton("Reload")
        self.preload_btn.clicked.connect(self.start_model_preload)
        preload_row.addWidget(self.preload_btn)
        left_layout.addLayout(preload_row)

        overlay_header = QLabel("Overlay Settings")
        overlay_header.setObjectName("sectionHeader")
        left_layout.addWidget(overlay_header)

        self.overlay_enabled_checkbox = QCheckBox("Enable overlay")
        self.overlay_enabled_checkbox.setChecked(True)
        self.overlay_enabled_checkbox.toggled.connect(self.on_overlay_enabled_changed)
        left_layout.addWidget(self.overlay_enabled_checkbox)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Opacity"))
        self.overlay_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.overlay_opacity_slider.setRange(35, 100)
        self.overlay_opacity_slider.setValue(90)
        self.overlay_opacity_slider.valueChanged.connect(self.on_overlay_opacity_changed)
        opacity_row.addWidget(self.overlay_opacity_slider)
        left_layout.addLayout(opacity_row)

        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Font Size"))
        self.overlay_font_slider = QSlider(Qt.Orientation.Horizontal)
        self.overlay_font_slider.setRange(14, 42)
        self.overlay_font_slider.setValue(24)
        self.overlay_font_slider.valueChanged.connect(self.on_overlay_font_size_changed)
        font_row.addWidget(self.overlay_font_slider)
        left_layout.addLayout(font_row)

        position_row = QHBoxLayout()
        position_row.addWidget(QLabel("Position"))
        self.overlay_position_combo = QComboBox()
        self.overlay_position_combo.addItems(["Top", "Bottom", "Custom"])
        self.overlay_position_combo.setCurrentText("Bottom")
        self.overlay_position_combo.currentTextChanged.connect(self.on_overlay_position_changed)
        position_row.addWidget(self.overlay_position_combo)
        left_layout.addLayout(position_row)

        self.overlay_clickthrough_checkbox = QCheckBox("Click-through overlay")
        self.overlay_clickthrough_checkbox.toggled.connect(self.on_overlay_clickthrough_changed)
        left_layout.addWidget(self.overlay_clickthrough_checkbox)

        attach_header = QLabel("Window Attachment")
        attach_header.setObjectName("sectionHeader")
        left_layout.addWidget(attach_header)

        self.window_label_text = QLabel("Target Window")
        left_layout.addWidget(self.window_label_text)

        self.window_combo = QComboBox()
        self.window_combo.setMinimumHeight(34)
        self.window_combo.setFont(QFont("Bahnschrift", 11))
        left_layout.addWidget(self.window_combo)

        attach_row = QHBoxLayout()
        self.refresh_windows_btn = QPushButton("Refresh Windows")
        self.refresh_windows_btn.clicked.connect(self.refresh_windows)
        attach_row.addWidget(self.refresh_windows_btn)
        self.detect_device_btn = QPushButton("Detect Device")
        self.detect_device_btn.clicked.connect(self.detect_current_audio_device)
        attach_row.addWidget(self.detect_device_btn)
        left_layout.addLayout(attach_row)

        self.launch_overlay_btn = QPushButton("Attach Overlay")
        self.launch_overlay_btn.clicked.connect(self.launch_overlay)
        left_layout.addWidget(self.launch_overlay_btn)

        self.close_overlay_btn = QPushButton("Detach Overlay")
        self.close_overlay_btn.clicked.connect(self.close_overlay)
        left_layout.addWidget(self.close_overlay_btn)

        trans_header = QLabel("Translation Options")
        trans_header.setObjectName("sectionHeader")
        left_layout.addWidget(trans_header)

        self.translation_enabled_checkbox = QCheckBox("Enable translation")
        self.translation_enabled_checkbox.setChecked(True)
        self.translation_enabled_checkbox.toggled.connect(self.on_translation_enabled_toggled)
        left_layout.addWidget(self.translation_enabled_checkbox)

        pivot_row = QHBoxLayout()
        pivot_row.addWidget(QLabel("Pivot mode:"))
        self.pivot_combo = QComboBox()
        self.pivot_combo.addItems(["Direct", "Via English"])
        self.pivot_combo.setEnabled(False)
        pivot_row.addWidget(self.pivot_combo)
        left_layout.addLayout(pivot_row)

        left_layout.addStretch()

        right_frame = QFrame()
        right_layout = QVBoxLayout(right_frame)
        right_layout.setSpacing(8)
        right_layout.setContentsMargins(14, 14, 14, 14)

        right_header = QLabel("Live Output")
        right_header.setObjectName("sectionHeader")
        right_layout.addWidget(right_header)

        self.timestamp_checkbox = QCheckBox("Show timestamps")
        self.timestamp_checkbox.setChecked(True)
        right_layout.addWidget(self.timestamp_checkbox)

        right_layout.addWidget(QLabel("Transcription"))
        self.transcription_text = QTextEdit()
        self.transcription_text.setReadOnly(True)
        self.transcription_text.setMinimumHeight(180)
        self.transcription_text.setFont(QFont("Consolas", 10))
        right_layout.addWidget(self.transcription_text)

        right_layout.addWidget(QLabel("Translation"))
        self.translation_text = QTextEdit()
        self.translation_text.setReadOnly(True)
        self.translation_text.setMinimumHeight(180)
        self.translation_text.setFont(QFont("Consolas", 10))
        right_layout.addWidget(self.translation_text)

        controls_row = QHBoxLayout()
        self.clear_output_btn = QPushButton("Clear")
        self.clear_output_btn.clicked.connect(self.clear_output)
        controls_row.addWidget(self.clear_output_btn)
        self.copy_output_btn = QPushButton("Copy")
        self.copy_output_btn.clicked.connect(self.copy_output)
        controls_row.addWidget(self.copy_output_btn)
        self.save_output_btn = QPushButton("Save")
        self.save_output_btn.clicked.connect(self.save_output)
        controls_row.addWidget(self.save_output_btn)
        controls_row.addStretch()
        right_layout.addLayout(controls_row)

        right_layout.addWidget(QLabel("Events"))
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setMinimumHeight(120)
        self.output_text.setFont(QFont("Consolas", 9))
        right_layout.addWidget(self.output_text)

        splitter.addWidget(self.settings_frame)
        splitter.addWidget(right_frame)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([460, 680])
        layout.addWidget(splitter, 1)

        session_frame = QFrame()
        session_layout = QVBoxLayout(session_frame)
        session_layout.setContentsMargins(12, 10, 12, 10)
        session_layout.setSpacing(8)

        self.status_display = QLabel("Ready. Select a source window to begin.")
        self.status_display.setFont(QFont("Bahnschrift", 10))
        self.status_display.setStyleSheet("color: #10b981; font-weight: bold;")
        session_layout.addWidget(self.status_display)

        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setMinimumHeight(8)
        self.loading_bar.hide()
        session_layout.addWidget(self.loading_bar)
        layout.addWidget(session_frame)

        self.bottom_state_label = QLabel("Idle")
        self.bottom_model_label = QLabel("Model: Vosk")
        self.bottom_latency_label = QLabel("Latency: -- ms")
        self.bottom_audio_label = QLabel("Input: 0%")
        self.bottom_alert_label = QLabel("OK")
        self.statusBar().addPermanentWidget(self.bottom_state_label)
        self.statusBar().addPermanentWidget(self.bottom_model_label)
        self.statusBar().addPermanentWidget(self.bottom_latency_label)
        self.statusBar().addPermanentWidget(self.bottom_audio_label)
        self.statusBar().addPermanentWidget(self.bottom_alert_label)
        self.statusBar().showMessage("Ready")

    def update_selection_summary(self):
        source = self.source_combo.currentText()
        target = self.target_combo.currentText()
        mode = self.mode_combo.currentText() if hasattr(self, "mode_combo") else "Real-time"
        self.selection_summary.setText(f"Mode: {mode} | Language Flow: {source} -> {target}")

    def on_mode_changed(self, mode_text: str):
        if mode_text == "File transcription":
            self.log_output("warning", "File transcription UI is reserved for a future offline module.")
            self.statusBar().showMessage("File transcription is not available yet")
        self.update_selection_summary()

    def on_theme_toggled(self, checked: bool):
        self.is_dark_mode = checked
        self.setStyleSheet(self.get_stylesheet())

    def open_settings_dialog(self):
        QMessageBox.information(
            self,
            "Settings",
            "Settings modal placeholder:\n\n"
            "- Model paths\n"
            "- Performance tuning\n"
            "- Default language\n"
            "- GPU toggle (future)",
        )

    def on_overlay_enabled_changed(self, checked: bool):
        self.overlay_enabled = checked
        if not checked:
            self.close_overlay()

    def on_overlay_opacity_changed(self, value: int):
        opacity = max(0.35, min(1.0, value / 100.0))
        self.controller.overlay_manager.config.opacity = opacity
        if self.controller.overlay_manager.overlay:
            self.controller.overlay_manager.overlay.setWindowOpacity(opacity)

    def on_overlay_font_size_changed(self, value: int):
        self.controller.overlay_manager.config.font_size = max(8, int(value))
        if self.controller.overlay_manager.overlay:
            self.controller.overlay_manager.overlay.update()

    def on_overlay_position_changed(self, value: str):
        self.current_overlay_position = value
        self.apply_overlay_position()

    def on_overlay_clickthrough_changed(self, checked: bool):
        overlay = self.controller.overlay_manager.overlay
        if overlay:
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, checked)

    def on_translation_enabled_toggled(self, checked: bool):
        self.target_combo.setEnabled(not self.is_running)
        self.pivot_combo.setEnabled(checked)
        if not checked:
            self.target_combo.setCurrentText("None")
        elif self.target_combo.currentText() == "None":
            self.target_combo.setCurrentText("Auto Opposite")
        self.update_selection_summary()

    def on_target_language_changed(self, value: str):
        should_enable = value != "None"
        if self.translation_enabled_checkbox.isChecked() != should_enable:
            self.translation_enabled_checkbox.blockSignals(True)
            self.translation_enabled_checkbox.setChecked(should_enable)
            self.translation_enabled_checkbox.blockSignals(False)
            self.pivot_combo.setEnabled(should_enable)
        self.update_selection_summary()

    def clear_output(self):
        self.transcription_text.clear()
        self.translation_text.clear()
        self.output_text.clear()

    def copy_output(self):
        combined = (
            "Transcription\n"
            + self.transcription_text.toPlainText()
            + "\n\nTranslation\n"
            + self.translation_text.toPlainText()
        )
        QApplication.clipboard().setText(combined)
        self.statusBar().showMessage("Live output copied to clipboard", 1800)

    def save_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Transcript",
            "transcript.txt",
            "Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return

        content = (
            "Transcription\n"
            + self.transcription_text.toPlainText()
            + "\n\nTranslation\n"
            + self.translation_text.toPlainText()
            + "\n\nEvents\n"
            + self.output_text.toPlainText()
        )
        with open(path, "w", encoding="utf-8") as file_handle:
            file_handle.write(content)
        self.statusBar().showMessage(f"Saved transcript to {path}", 2200)

    def apply_overlay_position(self):
        overlay = self.controller.overlay_manager.overlay
        target_window = self.window_combo.currentData()
        if overlay is None or target_window is None:
            return

        if self.current_overlay_position == "Custom":
            return

        rect = QRect(target_window.x, target_window.y, target_window.width, target_window.height)
        width = overlay.width()
        height = overlay.height()
        x = rect.x() + max(20, (rect.width() - width) // 2)
        if self.current_overlay_position == "Top":
            y = rect.y() + 24
        else:
            y = rect.y() + max(20, rect.height() - height - 48)
        overlay.move(x, y)

    def preload_default_models_blocking(self) -> tuple[bool, str]:
        languages = self.get_preload_languages_for_source()
        languages = [lang for lang in languages if lang in {"en", "hi"}]
        if not languages:
            self.models_ready = False
            self.start_btn.setEnabled(False)
            return False, "No speech models available"

        try:
            self.set_loading_state(True, "Loading default speech models...")
            QApplication.processEvents()
            started = time.perf_counter()
            self.controller.realtime_stt.prepare_languages(languages)
            elapsed = time.perf_counter() - started

            self.models_ready = True
            self.start_btn.setEnabled(True)
            self.set_loading_state(False)
            self.statusBar().showMessage("Models ready")
            self.bottom_model_label.setText("Model: Vosk (ready)")
            return True, f"Models ready ({elapsed:.1f}s)"
        except Exception as exc:
            self.models_ready = False
            self.start_btn.setEnabled(False)
            self.set_loading_state(False)
            self.statusBar().showMessage("Model load failed")
            self.bottom_model_label.setText("Model: load failed")
            return False, f"Model load failed: {exc}"

    def refresh_windows(self):
        windows = self.controller.window_tracker.get_available_windows()
        current_data = self.window_combo.currentData()

        selected_key = None
        selected_title = None
        if current_data is not None:
            selected_key = (
                getattr(current_data, "title", ""),
                getattr(current_data, "x", 0),
                getattr(current_data, "y", 0),
                getattr(current_data, "width", 0),
                getattr(current_data, "height", 0),
            )
            selected_title = getattr(current_data, "title", "")

        self.window_combo.blockSignals(True)
        self.window_combo.clear()

        selected_index = -1
        for window in windows:
            display_text = f"{window.title[:70]}" if len(window.title) > 70 else window.title
            self.window_combo.addItem(display_text, window)
            window_key = (window.title, window.x, window.y, window.width, window.height)
            if selected_key is not None and window_key == selected_key:
                selected_index = self.window_combo.count() - 1

        if selected_index < 0 and selected_title:
            for i in range(self.window_combo.count()):
                data = self.window_combo.itemData(i)
                if data and getattr(data, "title", "") == selected_title:
                    selected_index = i
                    break

        if selected_index >= 0:
            self.window_combo.setCurrentIndex(selected_index)

        self.window_combo.blockSignals(False)

    def get_preload_languages_for_source(self) -> list[str]:
        available = self.controller.get_available_languages()
        source_map = {
            "Auto Detect": ["en"] if "en" in available else available[:1],
            "English": ["en"] if "en" in available else [],
            "Hindi": ["hi"] if "hi" in available else [],
        }
        return source_map.get(self.source_combo.currentText(), ["en"] if "en" in available else available[:1])

    def refresh_microphone_devices(self):
        previous_device_id = self.mic_combo.currentData() if hasattr(self, "mic_combo") else None
        candidates = AudioInputHandler.get_microphone_candidates()

        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        self.mic_combo.addItem("Auto (System default)", None)

        selected_index = 0
        for candidate in candidates:
            device_id = candidate.get("device_id")
            display_name = str(candidate.get("name", "Microphone"))
            self.mic_combo.addItem(f"{display_name} (id={device_id})", device_id)
            if previous_device_id is not None and device_id == previous_device_id:
                selected_index = self.mic_combo.count() - 1

        self.mic_combo.setCurrentIndex(selected_index)
        self.mic_combo.blockSignals(False)

    def on_capture_mode_changed(self, text: str):
        mic_mode = text in {"Microphone", "Virtual Device"}
        self.mic_label.setVisible(mic_mode)
        self.mic_combo.setVisible(mic_mode)
        self.refresh_mic_btn.setVisible(mic_mode)
        self.voice_focus_checkbox.setVisible(mic_mode)
        self.mic_combo.setEnabled(mic_mode and not self.is_running)
        self.refresh_mic_btn.setEnabled(mic_mode and not self.is_running)
        self.voice_focus_checkbox.setEnabled(mic_mode and not self.is_running)

    def on_source_language_changed(self, _text: str):
        languages = self.get_preload_languages_for_source()
        if not languages:
            return
        if all(self.controller.realtime_stt.is_language_ready(lang) for lang in languages):
            self.models_ready = True
            self.start_btn.setEnabled(not self.is_running and not self.is_stopping)
            self.model_status.setText("Ready")
            self.model_status.setStyleSheet("color: #10b981; font-weight: bold;")
            self.set_loading_state(False)
            return
        self.start_model_preload(languages)

    def set_loading_state(self, is_loading: bool, message: str | None = None):
        self.loading_bar.setVisible(is_loading)
        if is_loading:
            self.status_display.setText(message or "Loading...")
            self.status_display.setStyleSheet("color: #f59e0b; font-weight: bold;")
        elif not self.is_running and not self.is_stopping:
            self.status_display.setText("✓ Ready - Select a window to start")
            self.status_display.setStyleSheet("color: #10b981; font-weight: bold;")

    def start_model_preload(self, languages: list[str] | None = None):
        languages = languages or self.get_preload_languages_for_source()
        if self.preload_thread and self.preload_thread.isRunning():
            return
        if not languages:
            return
        if all(self.controller.realtime_stt.is_language_ready(lang) for lang in languages):
            self.models_ready = True
            self.start_btn.setEnabled(not self.is_running and not self.is_stopping)
            self.model_status.setText(f"Ready ({langs_text})")
            self.model_status.setStyleSheet("color: #10b981; font-weight: bold;")
            self.set_loading_state(False)
            return

        self.models_ready = False
        self.start_btn.setEnabled(False)
        self.preload_btn.setEnabled(False)
        langs_text = ", ".join(lang.upper() for lang in languages)
        self.set_loading_state(True, f"Loading {langs_text} models...")
        self.model_status.setText(f"Loading {langs_text}...")
        self.model_status.setStyleSheet("color: #f59e0b; font-weight: bold;")
        self.statusBar().showMessage("Loading speech models...")
        self.log_output("status", f"⏳ Loading speech models: {langs_text}")

        self.preload_thread = ModelPreloadWorker(self.controller, languages)
        self.preload_thread.finished_signal.connect(self.on_model_preload_finished)
        self.preload_thread.error_signal.connect(self.on_model_preload_error)
        self.preload_thread.start()

    def on_model_preload_finished(self, elapsed: float, languages: list):
        self.models_ready = True
        self.preload_btn.setEnabled(True)
        self.start_btn.setEnabled(not self.is_running and not self.is_stopping)
        langs_text = ", ".join(lang.upper() for lang in languages) if languages else "none"
        self.set_loading_state(False)
        self.model_status.setText(f"Ready {langs_text} ({elapsed:.1f}s)")
        self.model_status.setStyleSheet("color: #10b981; font-weight: bold;")
        self.statusBar().showMessage("Speech models ready")
        self.bottom_model_label.setText("Model: Vosk (ready)")
        self.log_output("status", f"✅ Speech models ready ({elapsed:.1f}s) - {langs_text}")
        self.preload_thread = None

    def on_model_preload_error(self, error_msg: str):
        self.models_ready = False
        self.preload_btn.setEnabled(True)
        self.start_btn.setEnabled(False)
        self.set_loading_state(False)
        self.model_status.setText("Load failed")
        self.model_status.setStyleSheet("color: #ef4444; font-weight: bold;")
        self.statusBar().showMessage("Speech model load failed")
        self.bottom_model_label.setText("Model: load failed")
        self.log_output("error", f"❌ Speech model load failed: {error_msg}")
        self.preload_thread = None

    def get_settings(self) -> tuple[str, str, str, Optional[WindowInfo], Optional[int], bool]:
        source_map = {
            "Auto Detect": "auto",
            "English": "en",
            "Hindi": "hi"
        }
        capture_map = {
            "Microphone": "mic",
            "System Audio": "system",
            "Virtual Device": "mic",
        }
        target_map = {
            "None": "none",
            "English": "en",
            "Hindi": "hi",
            "Auto Opposite": "other",
        }
        src_lang = source_map.get(self.source_combo.currentText(), "auto")
        capture_mode = capture_map.get(self.capture_combo.currentText(), "mic")
        target_lang = target_map.get(self.target_combo.currentText(), "none")
        target_window = self.window_combo.currentData()
        preferred_mic_device_id = self.mic_combo.currentData() if capture_mode == "mic" else None
        mic_voice_focus = bool(self.voice_focus_checkbox.isChecked()) if capture_mode == "mic" else False
        return src_lang, capture_mode, target_lang, target_window, preferred_mic_device_id, mic_voice_focus

    def detect_current_audio_device(self):
        default_in_id, default_out_id = AudioInputHandler.get_default_device_ids()
        devices = AudioInputHandler.get_all_devices()

        capture_mode = self.capture_combo.currentText()
        if capture_mode in {"Microphone", "Virtual Device"}:
            if default_in_id is None:
                mic_candidates = AudioInputHandler.get_microphone_candidates()
                if mic_candidates:
                    fallback = mic_candidates[0]
                    self.log_output(
                        "status",
                        f"🎤 Default mic not exposed by Python; using best available: {fallback.get('name', 'Unknown')} (id={fallback.get('device_id')})",
                    )
                else:
                    self.log_output("warning", "⚠ No usable microphone device detected by Python audio backend.")
                return
            if 0 <= default_in_id < len(devices):
                dev_name = str(devices[default_in_id].get("name", "Unknown mic device"))
                self.log_output("status", f"🎤 Current microphone device: {dev_name} (id={default_in_id})")
            else:
                self.log_output("warning", "⚠ Default microphone index is out of range.")
            return

        if default_out_id is None:
            sys_candidates = AudioInputHandler.get_system_audio_candidates()
            if sys_candidates:
                fallback = sys_candidates[0]
                self.log_output(
                    "status",
                    f"🔊 Default output not exposed by Python; using best available: {fallback.get('name', 'Unknown')} (id={fallback.get('device_id')})",
                )
            else:
                self.log_output("warning", "⚠ No output device detected for system-audio transcription.")
            return

        if 0 <= default_out_id < len(devices):
            dev_name = str(devices[default_out_id].get("name", "Unknown output device"))
            self.log_output("status", f"🔊 Current output device: {dev_name} (id={default_out_id})")
        else:
            self.log_output("warning", "⚠ Default output device index is out of range.")

    def launch_overlay(self):
        if not self.overlay_enabled:
            self.log_output("status", "Overlay disabled in settings.")
            return

        target_window = self.window_combo.currentData()
        try:
            self.controller.overlay_manager.initialize()
            if target_window:
                target_rect = QRect(target_window.x, target_window.y, target_window.width, target_window.height)
                self.controller.overlay_manager.set_target_window(target_rect)
            self.on_overlay_opacity_changed(self.overlay_opacity_slider.value())
            self.on_overlay_font_size_changed(self.overlay_font_slider.value())
            self.on_overlay_clickthrough_changed(self.overlay_clickthrough_checkbox.isChecked())
            self.apply_overlay_position()
            self.controller.overlay_manager.set_subtitle("Overlay ready")
            self.controller.overlay_manager.show()
            self.log_output("status", "🪟 Overlay launched.")
        except Exception as exc:
            self.log_output("error", f"❌ Failed to launch overlay: {exc}")

    def close_overlay(self):
        try:
            if self.controller.overlay_manager:
                self.controller.overlay_manager.close()
            self.log_output("status", "🪟 Overlay closed.")
        except Exception as exc:
            self.log_output("error", f"❌ Failed to close overlay: {exc}")

    def on_start_clicked(self):
        if self.is_running:
            self.stop_transcription()
        else:
            self.start_transcription()

    def start_transcription(self):
        if self.mode_combo.currentText() != "Real-time":
            self.log_output("warning", "File transcription mode is not implemented yet.")
            self.statusBar().showMessage("Switch mode to Real-time", 2000)
            return

        src_lang, capture_mode, target_lang, target_window, preferred_mic_device_id, mic_voice_focus = self.get_settings()

        if not self.models_ready:
            self.log_output("warning", "⚠ Speech models are still loading. Please wait for readiness before starting.")
            self.statusBar().showMessage("Speech models are still loading")
            return

        if not target_window:
            self.log_output("error", "❌ Please select a window to transcribe")
            self.statusBar().showMessage("No window selected")
            return

        available = self.controller.get_available_languages()
        if src_lang in {"en", "hi"} and src_lang not in available:
            self.log_output("error", f"❌ Language Error: {src_lang.upper()} model not found. Available: {', '.join(available) or 'none'}")
            return
        
        if src_lang == "auto" and not available:
            self.log_output("error", "❌ No speech-to-text models found. Please check models/ directory.")
            return

        self.clear_output()
        self.log_output("status", f"🎙️ Capturing audio from: {target_window.title}")
        self.log_output("status", f"Source: {src_lang}")
        self.log_output("status", f"Audio input: {capture_mode}")
        if capture_mode == "mic":
            selected_text = self.mic_combo.currentText()
            self.log_output("status", f"Microphone device: {selected_text}")
            self.log_output("status", f"Voice focus: {'ON' if mic_voice_focus else 'OFF'}")
        self.log_output("status", f"Translation: {target_lang}")
        self.controller.realtime_stt.reset_recognizers()

        self.is_running = True
        self.is_stopping = False
        self.last_stt_event_ts = None
        self.bottom_state_label.setText("Listening")
        self.bottom_alert_label.setText("OK")
        
        self.window_refresh_timer.stop()
        
        self.start_btn.setText("Stop")
        self.start_btn.setEnabled(True)
        self.start_btn.setObjectName("primaryButton")
        self.start_btn.setStyleSheet(
            "QPushButton#primaryButton { background-color: #dc2626; color: #ffffff; border: 1px solid #ef4444; }"
            "QPushButton#primaryButton:hover { background-color: #ef4444; }"
        )
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)
        self.source_combo.setEnabled(False)
        self.capture_combo.setEnabled(False)
        self.mic_combo.setEnabled(False)
        self.refresh_mic_btn.setEnabled(False)
        self.voice_focus_checkbox.setEnabled(False)
        self.mode_combo.setEnabled(False)
        self.theme_toggle.setEnabled(False)
        self.settings_btn.setEnabled(False)
        self.translation_enabled_checkbox.setEnabled(False)
        self.target_combo.setEnabled(False)
        self.window_combo.setEnabled(False)
        self.status_display.setText("● RECORDING FROM WINDOW...")
        self.status_display.setStyleSheet("color: #dc2626; font-weight: bold;")
        self.statusBar().showMessage(f"Recording from: {target_window.title}")

        try:
            self.controller.overlay_manager.initialize()
            target_rect = QRect(
                target_window.x,
                target_window.y,
                target_window.width,
                target_window.height
            )
            self.controller.overlay_manager.set_target_window(target_rect)
            self.controller.overlay_manager.show()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.log_output("warning", f"⚠ Overlay setup failed: {e}")

        self.worker_thread = WindowAudioWorker(
            self.controller,
            src_lang,
            capture_mode,
            target_lang,
            target_window,
            preferred_mic_device_id=preferred_mic_device_id,
            mic_voice_focus=mic_voice_focus,
        )
        self.worker_thread.message_signal.connect(self.log_output)
        self.worker_thread.subtitle_signal.connect(self.on_subtitle_received)
        self.worker_thread.clear_subtitle_signal.connect(self.on_subtitle_clear)
        self.worker_thread.audio_level_signal.connect(self.on_audio_level)
        self.worker_thread.error_signal.connect(self.on_error)
        self.worker_thread.finished_signal.connect(self.on_transcription_finished)
        self.worker_thread.start()

        if self.tray_icon.isVisible():
            self.tray_icon.showMessage(
                "Transcription Active",
                f"Recording from: {target_window.title}",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )

    def stop_transcription(self):
        if not self.worker_thread or self.is_stopping:
            return

        self.is_stopping = True
        self.bottom_state_label.setText("Processing")
        self.log_output("status", "⏹ Stopping transcription...")
        self.start_btn.setEnabled(False)
        self.status_display.setText("◌ STOPPING...")
        self.status_display.setStyleSheet("color: #f59e0b; font-weight: bold;")
        self.statusBar().showMessage("Stopping transcription...")
        self.worker_thread.stop()

    def on_transcription_finished(self):
        if not self.is_running and not self.is_stopping and self.worker_thread is None:
            return

        self.is_running = False
        self.is_stopping = False
        
        try:
            if self.controller.overlay_manager:
                self.controller.overlay_manager.close()
        except Exception:
            pass
        
        self.window_refresh_timer.start(3000)
        
        self.start_btn.setText("Start")
        self.start_btn.setEnabled(self.models_ready)
        self.start_btn.setStyleSheet("")
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)
        self.source_combo.setEnabled(True)
        self.capture_combo.setEnabled(True)
        self.on_capture_mode_changed(self.capture_combo.currentText())
        self.mode_combo.setEnabled(True)
        self.theme_toggle.setEnabled(True)
        self.settings_btn.setEnabled(True)
        self.translation_enabled_checkbox.setEnabled(True)
        self.target_combo.setEnabled(True)
        self.window_combo.setEnabled(True)
        self.status_display.setText("✓ Ready - Select a window to start")
        self.status_display.setStyleSheet("color: #10b981; font-weight: bold;")
        self.statusBar().showMessage("Ready")
        self.log_output("status", "✓ Transcription stopped")
        self.bottom_state_label.setText("Idle")
        self.bottom_latency_label.setText("Latency: -- ms")
        self.audio_level_bar.setValue(0)
        self.bottom_audio_label.setText("Input: 0%")
        self.worker_thread = None
        
        if self.tray_icon.isVisible():
            self.tray_icon.setToolTip("Offline Caption Studio - Ready")

    def on_error(self, error_msg: str):
        self.log_output("error", f"❌ Error: {error_msg}")
        self.bottom_alert_label.setText("Warning")

    def on_audio_level(self, level: int):
        safe_level = max(0, min(100, int(level)))
        self.audio_level_bar.setValue(safe_level)
        self.bottom_audio_label.setText(f"Input: {safe_level}%")

    def on_subtitle_received(self, text: str):
        if not self.overlay_enabled:
            return
        try:
            if self.controller.overlay_manager and self.controller.overlay_manager.overlay:
                self.controller.overlay_manager.set_subtitle(text)
                self.apply_overlay_position()
                self.controller.overlay_manager.show()
        except Exception:
            pass

    def on_subtitle_clear(self):
        try:
            if self.controller.overlay_manager:
                self.controller.overlay_manager.clear_subtitle()
        except Exception:
            pass

    def log_output(self, kind: str, text: str):
        color_map = {
            "status": "#0084ff",
            "stt": "#10b981",
            "stt_partial": "#6366f1",
            "debug": "#9ca3af",
            "warning": "#f59e0b",
            "error": "#ef4444",
        }

        timestamp = time.strftime("%H:%M:%S")
        timestamp_prefix = f"[{timestamp}] " if self.timestamp_checkbox.isChecked() else ""
        color = color_map.get(kind, "#2c3e50")

        if kind in {"status", "warning", "error"}:
            self.output_text.append(
                f"<span style='color: #7f8c8d'>{timestamp_prefix}</span> "
                f"<span style='color: {color}'>{text}</span>"
            )

        if kind == "error":
            self.bottom_alert_label.setText("Error")

        if kind in {"stt", "stt_partial"}:
            now = time.perf_counter()
            if self.last_stt_event_ts is not None:
                latency_ms = int((now - self.last_stt_event_ts) * 1000)
                self.bottom_latency_label.setText(f"Latency: {latency_ms} ms")
            self.last_stt_event_ts = now

            clean_text = text.strip()
            is_bracketed = clean_text.startswith("[") and "]" in clean_text
            lang_tag = ""
            body = clean_text
            if is_bracketed:
                split_at = clean_text.find("]")
                lang_tag = clean_text[1:split_at].strip().lower()
                body = clean_text[split_at + 1 :].strip()

            target_map = {
                "English": "en",
                "Hindi": "hi",
                "Auto Opposite": "other",
                "None": "none",
            }
            selected_target = target_map.get(self.target_combo.currentText(), "none")
            send_to_translation = False
            if self.translation_enabled_checkbox.isChecked() and selected_target in {"en", "hi"}:
                send_to_translation = lang_tag == selected_target

            pane = self.translation_text if send_to_translation else self.transcription_text
            text_color = "#94a3b8" if kind == "stt_partial" else "#ffffff"
            if not self.is_dark_mode:
                text_color = "#64748b" if kind == "stt_partial" else "#0f172a"

            display = f"{timestamp_prefix}{clean_text if clean_text else body}"
            pane.append(f"<span style='color: {text_color}'>{display}</span>")

    def closeEvent(self, event):
        if self.is_running and self.worker_thread:
            self.worker_thread.stop()
            self.worker_thread.wait(2000)
        if self.preload_thread and self.preload_thread.isRunning():
            self.preload_thread.quit()
            self.preload_thread.wait(1000)
        self.window_refresh_timer.stop()
        if self.tray_icon.isVisible():
            self.tray_icon.hide()
        event.accept()

    def changeEvent(self, event):
        super().changeEvent(event)
