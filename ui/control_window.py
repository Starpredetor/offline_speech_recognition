from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional
import numpy as np

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QTextEdit, QFrame, QSystemTrayIcon, QMenu, QApplication, QProgressBar
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QRect
from PySide6.QtGui import QFont
from PySide6.QtCore import QCoreApplication

from config import CONFIG
from core import TranscriptionController, AudioInputHandler
from core.window import WindowInfo


class WindowAudioWorker(QThread):
    message_signal = Signal(str, str)
    subtitle_signal = Signal(str)  # For overlay subtitle updates
    clear_subtitle_signal = Signal()  # Signal to clear overlay (pause detected)
    finished_signal = Signal()
    error_signal = Signal(str)

    def __init__(
        self,
        controller: TranscriptionController,
        src_lang: str,
        capture_mode: str,
        target_lang: str,
        target_window: Optional[WindowInfo],
    ):
        super().__init__()
        self.controller = controller
        self.src_lang = src_lang
        self.capture_mode = capture_mode
        self.target_lang = target_lang
        self.target_window = target_window
        self._stop_event = threading.Event()
        self._attempt_stop_event: Optional[threading.Event] = None

    def run(self):
        translation_executor: ThreadPoolExecutor | None = None
        if self.target_lang != "none":
            # Keep translation work off the hot audio callback path.
            translation_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="caption-translate")

        try:
            if not self.target_window:
                self.error_signal.emit("No window selected for audio capture")
                return

            safe_title = self.target_window.title.encode('ascii', errors='replace').decode('ascii')
            print(f"[DEBUG] Worker thread started for: {safe_title}")
            self.message_signal.emit("status", f"🎤 Capturing audio from: {self.target_window.title}")

            system_audio_candidates = AudioInputHandler.get_system_audio_candidates() if self.capture_mode == "system" else []
            mic_candidates = AudioInputHandler.get_microphone_candidates() if self.capture_mode == "mic" else []
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
            print(f"[DEBUG] Preloading recognizers for: {preload_langs}")
            self.controller.realtime_stt.prepare_languages(preload_langs)
            preload_elapsed = time.perf_counter() - preload_started
            print(f"[DEBUG] Recognizers ready in {preload_elapsed:.2f}s")
            self.message_signal.emit("status", f"✅ Speech model ready ({preload_elapsed:.1f}s)")
            if self._stop_event.is_set():
                print("[DEBUG] Stop requested during model preload")
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
            attempt_stop_event = threading.Event()
            attempt_started_at = time.monotonic()
            attempt_chunk_count = 0
            attempt_non_silent_count = 0
            attempt_output_count = 0
            attempt_failover_triggered = False
            silent_chunk_count = 0  # Track consecutive silent chunks for pause detection
            silence_threshold_chunks = 10  # ~0.8s of silence at 12ms per chunk triggers clear
            translation_cache: dict[tuple[str, str, str], str] = {}
            latest_translation_seq = 0
            latest_emitted_seq = 0

            def chunk_text(text: str, max_length: int = 90, break_on_punctuation: bool = True) -> list[str]:
                if len(text) <= max_length:
                    return [text]
                
                chunks = []
                if break_on_punctuation:
                    # Split on common sentence endings first
                    sentences = text.replace('? ', '?|').replace('! ', '!|').replace('. ', '.|').split('|')
                    current_chunk = ""
                    
                    for sentence in sentences:
                        test_chunk = (current_chunk + " " + sentence.strip()).strip()
                        if len(test_chunk) <= max_length:
                            current_chunk = test_chunk
                        else:
                            if current_chunk:
                                chunks.append(current_chunk)
                            current_chunk = sentence.strip()
                    
                    if current_chunk:
                        chunks.append(current_chunk)
                
                if not chunks:
                    # Fallback: break by word at max_length
                    words = text.split()
                    current_chunk = ""
                    for word in words:
                        if len(current_chunk) + len(word) + 1 <= max_length:
                            current_chunk += word + " " if current_chunk else word
                        else:
                            if current_chunk:
                                chunks.append(current_chunk.strip())
                            current_chunk = word
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                
                return chunks if chunks else [text]

            def emit_transcript(text: str, detected_lang: str, is_final: bool) -> None:
                nonlocal last_preview, silent_chunk_count
                nonlocal partial_count, final_count, attempt_output_count
                nonlocal latest_translation_seq, latest_emitted_seq
                if not text.strip():
                    return

                # For microphone mode, show only final recognition for cleaner, more accurate output.
                if self.capture_mode == "mic" and not is_final:
                    return

                source_text = text

                if not is_final and source_text == last_preview:
                    return

                attempt_output_count += 1
                silent_chunk_count = 0  

                if is_final:
                    final_count += 1
                    last_preview = ""
                    print(f"[STT:{detected_lang}] {source_text}")
                    self.subtitle_signal.emit(source_text)
                    self.message_signal.emit("stt", f"[{detected_lang.upper()}] {source_text}")
                else:
                    partial_count += 1
                    last_preview = source_text
                    preview_text = f"{source_text} ..."
                    print(f"[STT-PARTIAL:{detected_lang}] {source_text}")
                    self.message_signal.emit("stt_partial", f"[{detected_lang.upper()}] {preview_text}")

                if translation_executor is None or not is_final:
                    return

                resolved_target = self.controller._resolve_target(detected_lang, self.target_lang)
                if not resolved_target or resolved_target == detected_lang:
                    return

                cache_key = (detected_lang, resolved_target, source_text)
                cached_translation = translation_cache.get(cache_key)
                if cached_translation is not None:
                    self.subtitle_signal.emit(cached_translation)
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
                    self.subtitle_signal.emit(translated_text)
                    self.message_signal.emit("stt", f"[{to_lang.upper()}] {translated_text}")

                translation_future.add_done_callback(_on_translation_done)

            def process_chunk(chunk: bytes) -> None:
                nonlocal chunk_count, non_silent_chunk_count, last_debug_report, first_chunk_seen
                nonlocal attempt_chunk_count, attempt_non_silent_count, attempt_failover_triggered
                nonlocal silent_chunk_count
                chunk_count += 1
                attempt_chunk_count += 1
                first_chunk_seen = True

                pcm = np.frombuffer(chunk, dtype=np.int16)
                if pcm.size > 0:
                    avg_energy = float(np.mean(np.abs(pcm)))
                    if avg_energy >= input_energy_threshold:
                        non_silent_chunk_count += 1
                        attempt_non_silent_count += 1
                        silent_chunk_count = 0  # Reset silence counter on sound
                    else:
                        silent_chunk_count += 1
                        # Detect pause: accumulate silent chunks, flush on sustained silence
                        if silent_chunk_count >= silence_threshold_chunks:
                            if chunk_count > 10:  # Avoid clearing too early
                                print(f"[DEBUG] Pause detected ({silent_chunk_count} silent chunks)")
                                self.clear_subtitle_signal.emit()
                                silent_chunk_count = 0  # Reset after clearing
                else:
                    avg_energy = 0.0
                    silent_chunk_count += 1
                
                if chunk_count % 10 == 0:
                    print(f"[DEBUG] Received audio chunk #{chunk_count}, size: {len(chunk)} bytes")

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

                # Stick to selected source: do not auto-switch devices on silence.
                
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
                            print(f"[DEBUG] No final transcription yet (chunk #{chunk_count})")
                        return
                    is_final = state == "final"
                    detected_lang = self.src_lang

                if not text.strip():
                    print(f"[DEBUG] Received empty text")
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
                    attempt_started_at = time.monotonic()
                    attempt_chunk_count = 0
                    attempt_non_silent_count = 0
                    attempt_output_count = 0
                    attempt_failover_triggered = False
                    attempt_stop_event = threading.Event()
                    self._attempt_stop_event = attempt_stop_event
                    self.message_signal.emit(
                        "status",
                        f"🎧 Trying device {attempt_index}/{len(candidate_devices)}: {device_name}",
                    )
                    # Some WASAPI loopback devices reject specific channel counts; try safe fallbacks.
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
                            audio_input = AudioInputHandler(
                                sample_rate=self.controller.config.sample_rate,
                                channels=channel_value,
                                device_id=device_id,
                                capture_sample_rate=capture_sample_rate,
                                use_wasapi_loopback=use_wasapi_loopback,
                            )
                            print(
                                f"[DEBUG] Audio input initialized (attempt {attempt_index}, "
                                f"device={device_id}, loopback={use_wasapi_loopback}, channels={channel_value})"
                            )
                            print(f"[DEBUG] Starting audio stream...")
                            audio_input.stream_chunks(
                                callback=process_chunk,
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

                    print(f"[DEBUG] Audio stream ended")
                    self._attempt_stop_event = None
                    if attempt_failover_triggered:
                        continue
                    last_error = None
                    break
                except Exception as exc:
                    self._attempt_stop_event = None
                    last_error = exc
                    print(f"[DEBUG] Stream attempt failed for device {candidate.get('device_id')}: {exc}")
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
            print(f"[DEBUG] Error in worker thread: {e}")
            import traceback
            traceback.print_exc()
            self.error_signal.emit(str(e))
        finally:
            if translation_executor is not None:
                translation_executor.shutdown(wait=False, cancel_futures=True)
            print(f"[DEBUG] Worker thread finished")
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
        
        # Detect system theme
        self.is_dark_mode = self.detect_dark_mode()

        self.setWindowTitle("Window Audio Transcriber")
        self.setGeometry(100, 100, 900, 700)
        self.setStyleSheet(self.get_stylesheet())

        self.init_ui()
        
        # Setup system tray
        self.setup_tray()
        
        self.window_refresh_timer = QTimer()
        self.window_refresh_timer.timeout.connect(self.refresh_windows)
        self.window_refresh_timer.start(3000)
        
        self.refresh_windows()
        self.source_combo.currentTextChanged.connect(self.on_source_language_changed)
        self.start_model_preload(self.get_preload_languages_for_source())

    def detect_dark_mode(self) -> bool:
        app = QApplication.instance()
        if app:
            palette = app.palette()
            bg_color = palette.color(palette.ColorRole.Window)
            # If background is dark (low brightness), it's dark mode
            return bg_color.lightness() < 128
        return False

    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        
        # Create tray menu
        tray_menu = QMenu(self)
        
        restore_action = tray_menu.addAction("Restore")
        restore_action.triggered.connect(self.restore_from_tray)
        
        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_application)
        
        self.tray_icon.setContextMenu(tray_menu)
        
        # Use a simple default icon (system icon)
        try:
            self.tray_icon.setIcon(QApplication.style().standardIcon(
                QApplication.style().StandardPixmap.SP_MediaPlay
            ))
        except:
            pass
        
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.setToolTip("Window Audio Transcriber - Ready")
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
                    background-color: #1e1e1e;
                }
                
                QLabel {
                    color: #e0e0e0;
                }
                
                QComboBox {
                    background-color: #2d2d2d;
                    border: 2px solid #3d3d3d;
                    border-radius: 6px;
                    padding: 10px;
                    font-size: 14px;
                    font-family: "Segoe UI";
                    color: #e0e0e0;
                    selection-background-color: #1d4ed8;
                }
                
                QComboBox:hover {
                    border: 2px solid #4a9eff;
                }
                
                QComboBox::drop-down {
                    border: none;
                }
                
                QComboBox QAbstractItemView {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                    selection-background-color: #1d4ed8;
                }
                
                QTextEdit {
                    background-color: #252525;
                    border: 2px solid #3d3d3d;
                    border-radius: 6px;
                    padding: 10px;
                    font-family: "Courier New";
                    font-size: 12px;
                    color: #e0e0e0;
                }
                
                QStatusBar {
                    background-color: #2d2d2d;
                    border-top: 1px solid #3d3d3d;
                    color: #e0e0e0;
                }
                
                QFrame {
                    background-color: #2d2d2d;
                    border: 1px solid #3d3d3d;
                    border-radius: 8px;
                }
            """
        else:
            return """
                QMainWindow {
                    background-color: #f8f9fa;
                }
                
                QLabel {
                    color: #2c3e50;
                }
                
                QComboBox {
                    background-color: #ffffff;
                    border: 2px solid #e0e0e0;
                    border-radius: 6px;
                    padding: 10px;
                    font-size: 14px;
                    font-family: "Segoe UI";
                    color: #2c3e50;
                    selection-background-color: #1d4ed8;
                }
                
                QComboBox:hover {
                    border: 2px solid #1d4ed8;
                }
                
                QComboBox::drop-down {
                    border: none;
                }
                
                QComboBox QAbstractItemView {
                    background-color: #ffffff;
                    color: #2c3e50;
                    selection-background-color: #1d4ed8;
                }
                
                QTextEdit {
                    background-color: #f0f4f8;
                    border: 2px solid #e0e0e0;
                    border-radius: 6px;
                    padding: 10px;
                    font-family: "Courier New";
                    font-size: 12px;
                    color: #2c3e50;
                }
                
                QStatusBar {
                    background-color: #ffffff;
                    border-top: 1px solid #e0e0e0;
                    color: #2c3e50;
                }
                
                QFrame {
                    background-color: #ffffff;
                    border: 1px solid #e0e0e0;
                    border-radius: 8px;
                }
            """

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title = QLabel("🎙️ Window Audio Transcriber")
        title_font = QFont("Segoe UI", 16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Settings Panel
        settings_frame = QFrame()
        self.settings_frame = settings_frame
        settings_layout = QVBoxLayout(settings_frame)
        settings_layout.setSpacing(12)
        settings_layout.setContentsMargins(16, 16, 16, 16)

        lang_label = QLabel("Language Settings")
        lang_font = QFont("Segoe UI", 11)
        lang_font.setBold(True)
        lang_label.setFont(lang_font)
        settings_layout.addWidget(lang_label)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Source Language:"))
        self.source_combo = QComboBox()
        self.source_combo.addItems(["Auto Detect 🌐", "English 🇬🇧", "Hindi 🇮🇳"])
        self.source_combo.setMinimumHeight(35)
        self.source_combo.setMinimumWidth(200)
        lang_combo_font = QFont("Segoe UI", 13)
        self.source_combo.setFont(lang_combo_font)
        source_row.addWidget(self.source_combo)
        source_row.addStretch()
        settings_layout.addLayout(source_row)

        capture_row = QHBoxLayout()
        capture_row.addWidget(QLabel("Audio Source:"))
        self.capture_combo = QComboBox()
        self.capture_combo.addItems(["Microphone 🎤", "System Audio 🔊"])
        self.capture_combo.setMinimumHeight(35)
        self.capture_combo.setMinimumWidth(200)
        self.capture_combo.setFont(lang_combo_font)
        capture_row.addWidget(self.capture_combo)
        capture_row.addStretch()
        settings_layout.addLayout(capture_row)

        device_tools_row = QHBoxLayout()
        self.detect_device_btn = QPushButton("Detect Current Device")
        self.detect_device_btn.setMinimumHeight(35)
        self.detect_device_btn.setFont(QFont("Segoe UI", 10))
        self.detect_device_btn.clicked.connect(self.detect_current_audio_device)
        device_tools_row.addWidget(self.detect_device_btn)

        self.launch_overlay_btn = QPushButton("Launch Overlay")
        self.launch_overlay_btn.setMinimumHeight(35)
        self.launch_overlay_btn.setFont(QFont("Segoe UI", 10))
        self.launch_overlay_btn.clicked.connect(self.launch_overlay)
        device_tools_row.addWidget(self.launch_overlay_btn)

        self.close_overlay_btn = QPushButton("Close Overlay")
        self.close_overlay_btn.setMinimumHeight(35)
        self.close_overlay_btn.setFont(QFont("Segoe UI", 10))
        self.close_overlay_btn.clicked.connect(self.close_overlay)
        device_tools_row.addWidget(self.close_overlay_btn)
        device_tools_row.addStretch()
        settings_layout.addLayout(device_tools_row)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Translation:"))
        self.target_combo = QComboBox()
        self.target_combo.addItems(["None", "English", "Hindi", "Other"])
        self.target_combo.setMinimumHeight(35)
        self.target_combo.setMinimumWidth(200)
        self.target_combo.setFont(lang_combo_font)
        target_row.addWidget(self.target_combo)
        target_row.addStretch()
        settings_layout.addLayout(target_row)

        preload_row = QHBoxLayout()
        preload_row.addWidget(QLabel("Speech Models:"))
        self.model_status = QLabel("Loading...")
        self.model_status.setFont(QFont("Segoe UI", 10))
        self.model_status.setStyleSheet("color: #f59e0b; font-weight: bold;")
        preload_row.addWidget(self.model_status)
        self.preload_btn = QPushButton("Load Models")
        self.preload_btn.setMinimumHeight(35)
        self.preload_btn.setFont(QFont("Segoe UI", 10))
        self.preload_btn.clicked.connect(self.start_model_preload)
        preload_row.addWidget(self.preload_btn)
        preload_row.addStretch()
        settings_layout.addLayout(preload_row)

        window_label_text = QLabel("Select Window for Audio Capture:")
        window_label_text.setFont(QFont("Segoe UI", 10))
        settings_layout.addWidget(window_label_text)

        self.window_combo = QComboBox()
        self.window_combo.setMinimumHeight(35)
        window_combo_font = QFont("Segoe UI", 13)
        self.window_combo.setFont(window_combo_font)
        settings_layout.addWidget(self.window_combo)

        layout.addWidget(settings_frame)

        # Controls Panel
        controls_frame = QFrame()
        controls_layout = QVBoxLayout(controls_frame)
        controls_layout.setSpacing(12)
        controls_layout.setContentsMargins(16, 16, 16, 16)

        status_label = QLabel("Status")
        status_font = QFont("Segoe UI", 11)
        status_font.setBold(True)
        status_label.setFont(status_font)
        controls_layout.addWidget(status_label)

        self.status_display = QLabel("✓ Ready - Select a window to start")
        status_display_font = QFont("Segoe UI", 10)
        self.status_display.setFont(status_display_font)
        self.status_display.setStyleSheet("color: #10b981; font-weight: bold;")
        controls_layout.addWidget(self.status_display)

        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setMinimumHeight(10)
        self.loading_bar.hide()
        controls_layout.addWidget(self.loading_bar)

        controls_layout.addSpacing(8)

        button_layout = QHBoxLayout()
        
        self.start_btn = QPushButton("   ▶ START TRANSCRIPTION   ")
        self.start_btn.setMinimumHeight(45)
        start_btn_font = QFont("Segoe UI", 11)
        start_btn_font.setBold(True)
        self.start_btn.setFont(start_btn_font)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #1d4ed8;
                color: white;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #1e40af;
            }
            QPushButton:pressed {
                background-color: #1e3a8a;
            }
        """)
        self.start_btn.clicked.connect(self.on_start_clicked)
        self.start_btn.setEnabled(False)
        button_layout.addWidget(self.start_btn)
        
        controls_layout.addLayout(button_layout)
        layout.addWidget(controls_frame)

        # Output Panel
        output_frame = QFrame()
        output_layout = QVBoxLayout(output_frame)
        output_layout.setSpacing(8)
        output_layout.setContentsMargins(16, 16, 16, 16)

        output_label = QLabel("📝 Live Transcription")
        output_font = QFont("Segoe UI", 11)
        output_font.setBold(True)
        output_label.setFont(output_font)
        output_layout.addWidget(output_label)

        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setMinimumHeight(250)
        output_text_font = QFont("Courier New", 10)
        self.output_text.setFont(output_text_font)
        self.output_text.setStyleSheet("""
            QTextEdit {
                background-color: #f0f4f8;
                border: 2px solid #e0e0e0;
                border-radius: 6px;
                padding: 10px;
            }
        """)
        output_layout.addWidget(self.output_text)

        layout.addWidget(output_frame)

        # Status Bar
        self.statusBar().showMessage("Ready to start transcription")
        self.statusBar().setStyleSheet("""
            QStatusBar {
                background-color: #ffffff;
                border-top: 1px solid #e0e0e0;
            }
        """)

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
            "Auto Detect 🌐": ["en"] if "en" in available else available[:1],
            "English 🇬🇧": ["en"] if "en" in available else [],
            "Hindi 🇮🇳": ["hi"] if "hi" in available else [],
        }
        return source_map.get(self.source_combo.currentText(), ["en"] if "en" in available else available[:1])

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
        languages = [lang for lang in languages if lang in {"en", "hi"}]
        if self.preload_thread and self.preload_thread.isRunning():
            return
        if not languages:
            return
        if all(self.controller.realtime_stt.is_language_ready(lang) for lang in languages):
            self.models_ready = True
            self.start_btn.setEnabled(not self.is_running and not self.is_stopping)
            langs_text = ", ".join(lang.upper() for lang in languages)
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
        self.log_output("error", f"❌ Speech model load failed: {error_msg}")
        self.preload_thread = None

    def get_settings(self) -> tuple[str, str, str, Optional[WindowInfo]]:
        source_map = {
            "Auto Detect 🌐": "auto",
            "English 🇬🇧": "en",
            "Hindi 🇮🇳": "hi"
        }
        capture_map = {
            "Microphone 🎤": "mic",
            "System Audio 🔊": "system",
        }
        target_map = {
            "None": "none",
            "English": "en",
            "Hindi": "hi",
            "Other": "other",
        }
        src_lang = source_map.get(self.source_combo.currentText(), "auto")
        capture_mode = capture_map.get(self.capture_combo.currentText(), "mic")
        target_lang = target_map.get(self.target_combo.currentText(), "none")
        target_window = self.window_combo.currentData()
        return src_lang, capture_mode, target_lang, target_window

    def detect_current_audio_device(self):
        default_in_id, default_out_id = AudioInputHandler.get_default_device_ids()
        devices = AudioInputHandler.get_all_devices()

        capture_mode = self.capture_combo.currentText()
        if capture_mode == "Microphone 🎤":
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
        target_window = self.window_combo.currentData()
        try:
            self.controller.overlay_manager.initialize()
            if target_window:
                target_rect = QRect(target_window.x, target_window.y, target_window.width, target_window.height)
                self.controller.overlay_manager.set_target_window(target_rect)
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
        src_lang, capture_mode, target_lang, target_window = self.get_settings()

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

        self.output_text.clear()
        self.log_output("status", f"🎙️ Capturing audio from: {target_window.title}")
        self.log_output("status", f"Source: {src_lang}")
        self.log_output("status", f"Audio input: {capture_mode}")
        self.log_output("status", f"Translation: {target_lang}")
        self.controller.realtime_stt.reset_recognizers()

        self.is_running = True
        self.is_stopping = False
        
        # Stop window refresh timer to prevent auto-switching
        self.window_refresh_timer.stop()
        
        self.start_btn.setText("   ⏹ STOP TRANSCRIPTION   ")
        self.start_btn.setEnabled(True)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc2626;
                color: white;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #b91c1c;
            }
            QPushButton:pressed {
                background-color: #991b1b;
            }
        """)
        self.source_combo.setEnabled(False)
        self.capture_combo.setEnabled(False)
        self.target_combo.setEnabled(False)
        self.window_combo.setEnabled(False)
        self.status_display.setText("● RECORDING FROM WINDOW...")
        self.status_display.setStyleSheet("color: #dc2626; font-weight: bold;")
        self.statusBar().showMessage(f"Recording from: {target_window.title}")

        # Initialize overlay on main thread BEFORE starting worker
        try:
            print(f"[DEBUG] Starting overlay initialization")
            self.controller.overlay_manager.initialize()
            print(f"[DEBUG] Overlay initialized")
            target_rect = QRect(
                target_window.x,
                target_window.y,
                target_window.width,
                target_window.height
            )
            print(f"[DEBUG] Setting target window rect: {target_rect}")
            self.controller.overlay_manager.set_target_window(target_rect)
            print(f"[DEBUG] Showing overlay")
            self.controller.overlay_manager.show()
            print(f"[DEBUG] Overlay shown")
        except Exception as e:
            print(f"[DEBUG] Error during overlay setup: {e}")
            import traceback
            traceback.print_exc()
            self.log_output("warning", f"⚠ Overlay setup failed: {e}")

        self.worker_thread = WindowAudioWorker(self.controller, src_lang, capture_mode, target_lang, target_window)
        self.worker_thread.message_signal.connect(self.log_output)
        self.worker_thread.subtitle_signal.connect(self.on_subtitle_received)
        self.worker_thread.clear_subtitle_signal.connect(self.on_subtitle_clear)
        self.worker_thread.error_signal.connect(self.on_error)
        self.worker_thread.finished_signal.connect(self.on_transcription_finished)
        self.worker_thread.start()

        # Keep control window visible while transcription runs
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
        
        # Close overlay
        try:
            if self.controller.overlay_manager:
                self.controller.overlay_manager.close()
        except Exception:
            pass
        
        # Restart window refresh timer
        self.window_refresh_timer.start(3000)
        
        self.start_btn.setText("   ▶ START TRANSCRIPTION   ")
        self.start_btn.setEnabled(self.models_ready)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #1d4ed8;
                color: white;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: #1e40af;
            }
            QPushButton:pressed {
                background-color: #1e3a8a;
            }
        """)
        self.source_combo.setEnabled(True)
        self.capture_combo.setEnabled(True)
        self.target_combo.setEnabled(True)
        self.window_combo.setEnabled(True)
        self.status_display.setText("✓ Ready - Select a window to start")
        self.status_display.setStyleSheet("color: #10b981; font-weight: bold;")
        self.statusBar().showMessage("Ready")
        self.log_output("status", "✓ Transcription stopped")
        self.worker_thread = None
        
        # Update tray tooltip
        if self.tray_icon.isVisible():
            self.tray_icon.setToolTip("Window Audio Transcriber - Ready")

    def on_error(self, error_msg: str):
        self.log_output("error", f"❌ Error: {error_msg}")

    def on_subtitle_received(self, text: str):
        print(f"[DEBUG] on_subtitle_received called with: {text}")
        try:
            if self.controller.overlay_manager and self.controller.overlay_manager.overlay:
                print(f"[DEBUG] Updating overlay with text: {text}")
                self.controller.overlay_manager.set_subtitle(text)
                self.controller.overlay_manager.show()
                print(f"[DEBUG] Overlay updated and shown")
            else:
                print(f"[DEBUG] Overlay manager or overlay is None")
        except Exception as e:
            print(f"[DEBUG] Error updating overlay: {e}")
            import traceback
            traceback.print_exc()

    def on_subtitle_clear(self):
        print(f"[DEBUG] on_subtitle_clear called (pause detected)")
        try:
            if self.controller.overlay_manager:
                print(f"[DEBUG] Clearing overlay")
                self.controller.overlay_manager.clear_subtitle()
                print(f"[DEBUG] Overlay cleared")
            else:
                print(f"[DEBUG] Overlay manager is None")
        except Exception as e:
            print(f"[DEBUG] Error clearing overlay: {e}")
            import traceback
            traceback.print_exc()

    def log_output(self, kind: str, text: str):
        # Keep UI output concise: hide internal debug telemetry.
        if kind == "debug":
            return
        if kind == "stt_partial":
            return

        color_map = {
            "status": "#0084ff",
            "stt": "#10b981",
            "stt_partial": "#6366f1",
            "debug": "#9ca3af",
            "warning": "#f59e0b",
            "error": "#ef4444",
        }

        color = color_map.get(kind, "#2c3e50")
        timestamp = time.strftime("%H:%M:%S")
        self.output_text.append(f"<span style='color: #7f8c8d'>[{timestamp}]</span> <span style='color: {color}'>{text}</span>")

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
