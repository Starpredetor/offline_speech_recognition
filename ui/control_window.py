from __future__ import annotations

import threading
import time
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QTextEdit, QFrame, QSystemTrayIcon, QMenu, QApplication
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QRect
from PySide6.QtGui import QFont, QIcon
from PySide6.QtCore import QCoreApplication

from config import CONFIG
from core import TranscriptionController, AudioInputHandler
from core.window import WindowInfo


class WindowAudioWorker(QThread):
    message_signal = Signal(str, str)
    finished_signal = Signal()
    error_signal = Signal(str)

    def __init__(self, controller: TranscriptionController, src_lang: str, target_window: Optional[WindowInfo]):
        super().__init__()
        self.controller = controller
        self.src_lang = src_lang
        self.target_window = target_window
        self._stop_event = threading.Event()

    def run(self):
        try:
            if not self.target_window:
                self.error_signal.emit("No window selected for audio capture")
                return

            self.message_signal.emit("status", f"🎤 Capturing audio from: {self.target_window.title}")
            
            # Try to find loopback device for system audio, fall back to microphone
            loopback_device = AudioInputHandler.find_loopback_device()
            device_id = loopback_device
            
            if loopback_device is None:
                self.message_signal.emit("warning", "⚠ Stereo Mix not found, using microphone instead (recommend enabling Stereo Mix in Windows Sound Settings)")
            
            audio_input = AudioInputHandler(
                sample_rate=self.controller.config.sample_rate,
                channels=self.controller.config.channels,
                device_id=device_id,
            )

            # Initialize and position overlay on target window
            try:
                self.controller.overlay_manager.initialize()
                target_rect = QRect(
                    self.target_window.x,
                    self.target_window.y,
                    self.target_window.width,
                    self.target_window.height
                )
                self.controller.overlay_manager.set_target_window(target_rect)
            except Exception as e:
                self.message_signal.emit("warning", f"⚠ Overlay setup failed: {e}")

            def process_chunk(chunk: bytes) -> None:
                if self._stop_event.is_set():
                    audio_input.stop()
                    return

                if self.src_lang == "auto":
                    en_final, en_text = (False, "")
                    hi_final, hi_text = (False, "")

                    available = self.controller.get_available_languages()
                    if "en" in available:
                        en_final, en_text = self.controller.realtime_stt.accept_audio_chunk(chunk, lang="en")
                    if "hi" in available:
                        hi_final, hi_text = self.controller.realtime_stt.accept_audio_chunk(chunk, lang="hi")

                    if not en_final and not hi_final:
                        return

                    text, detected_lang = self.controller.language_detector.choose_best_candidate(
                        [("en", en_text if en_final else ""), ("hi", hi_text if hi_final else "")],
                        fallback="en",
                    )
                else:
                    is_final, text = self.controller.realtime_stt.accept_audio_chunk(chunk, lang=self.src_lang)
                    if not is_final or not text:
                        return
                    detected_lang = self.src_lang

                if text.strip():
                    # Update overlay on target window
                    try:
                        self.controller.overlay_manager.set_subtitle(text)
                        self.controller.overlay_manager.show()
                    except Exception:
                        pass
                    
                    # Also emit to main window
                    self.message_signal.emit("stt", f"[{detected_lang.upper()}] {text}")

            audio_input.stream_chunks(
                callback=process_chunk,
                stop_event=self._stop_event,
            )
        except Exception as e:
            self.error_signal.emit(str(e))
        finally:
            try:
                if hasattr(self.controller.overlay_manager, 'close'):
                    self.controller.overlay_manager.close()
            except:
                pass
            self.finished_signal.emit()

    def stop(self):
        self._stop_event.set()


class TranscriptionControlWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.controller = TranscriptionController(config=CONFIG)
        self.controller.setup_directories()

        self.worker_thread: Optional[WindowAudioWorker] = None
        self.is_running = False
        
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

    def detect_dark_mode(self) -> bool:
        """Detect if the system is using dark mode."""
        app = QApplication.instance()
        if app:
            palette = app.palette()
            bg_color = palette.color(palette.ColorRole.Window)
            # If background is dark (low brightness), it's dark mode
            return bg_color.lightness() < 128
        return False

    def setup_tray(self):
        """Setup system tray icon and menu."""
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
        """Handle tray icon clicks."""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            if self.isVisible():
                self.hide()
            else:
                self.restore_from_tray()

    def restore_from_tray(self):
        """Restore window from tray."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_application(self):
        """Quit the application."""
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

        self.window_combo.blockSignals(True)
        self.window_combo.clear()

        for window in windows:
            display_text = f"{window.title[:70]}" if len(window.title) > 70 else window.title
            self.window_combo.addItem(display_text, window)

        if current_data:
            idx = self.window_combo.findData(current_data)
            if idx >= 0:
                self.window_combo.setCurrentIndex(idx)

        self.window_combo.blockSignals(False)

    def get_settings(self) -> tuple[str, Optional[WindowInfo]]:
        source_map = {
            "Auto Detect 🌐": "auto",
            "English 🇬🇧": "en",
            "Hindi 🇮🇳": "hi"
        }
        src_lang = source_map.get(self.source_combo.currentText(), "auto")
        target_window = self.window_combo.currentData()
        return src_lang, target_window

    def on_start_clicked(self):
        if self.is_running:
            self.stop_transcription()
        else:
            self.start_transcription()

    def start_transcription(self):
        src_lang, target_window = self.get_settings()

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

        self.is_running = True
        
        # Stop window refresh timer to prevent auto-switching
        self.window_refresh_timer.stop()
        
        self.start_btn.setText("   ⏹ STOP TRANSCRIPTION   ")
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
        self.window_combo.setEnabled(False)
        self.status_display.setText("● RECORDING FROM WINDOW...")
        self.status_display.setStyleSheet("color: #dc2626; font-weight: bold;")
        self.statusBar().showMessage(f"Recording from: {target_window.title}")

        self.worker_thread = WindowAudioWorker(self.controller, src_lang, target_window)
        self.worker_thread.message_signal.connect(self.log_output)
        self.worker_thread.error_signal.connect(self.on_error)
        self.worker_thread.finished_signal.connect(self.on_transcription_finished)
        self.worker_thread.start()
        
        # Minimize to tray when transcription starts
        self.hide()
        if self.tray_icon.isVisible():
            self.tray_icon.showMessage(
                "Transcription Active",
                f"Recording from: {target_window.title}",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )

    def stop_transcription(self):
        if self.worker_thread:
            self.log_output("status", "⏹ Stopping transcription...")
            self.worker_thread.stop()
            self.worker_thread.wait(3000)

    def on_transcription_finished(self):
        self.is_running = False
        
        # Restart window refresh timer
        self.window_refresh_timer.start(3000)
        
        self.start_btn.setText("   ▶ START TRANSCRIPTION   ")
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
        self.window_combo.setEnabled(True)
        self.status_display.setText("✓ Ready - Select a window to start")
        self.status_display.setStyleSheet("color: #10b981; font-weight: bold;")
        self.statusBar().showMessage("Ready")
        self.log_output("status", "✓ Transcription stopped")
        
        # Update tray tooltip
        if self.tray_icon.isVisible():
            self.tray_icon.setToolTip("Window Audio Transcriber - Ready")

    def on_error(self, error_msg: str):
        self.log_output("error", f"❌ Error: {error_msg}")
        self.on_transcription_finished()

    def log_output(self, kind: str, text: str):
        # Don't show STT transcriptions in control window during active session
        if kind == "stt" and self.is_running:
            return
            
        color_map = {
            "status": "#0084ff",
            "stt": "#10b981",
            "warning": "#f59e0b",
            "error": "#ef4444",
        }

        color = color_map.get(kind, "#2c3e50")
        timestamp = time.strftime("%H:%M:%S")
        self.output_text.append(f"<span style='color: #7f8c8d'>[{timestamp}]</span> <span style='color: {color}'>{text}</span>")

    def closeEvent(self, event):
        if self.tray_icon.isVisible():
            self.hide()
            event.ignore()
        else:
            if self.is_running and self.worker_thread:
                self.worker_thread.stop()
                self.worker_thread.wait(2000)
            self.window_refresh_timer.stop()
            event.accept()

    def changeEvent(self, event):
        """Handle window state changes (minimization)."""
        if event.type() == event.Type.WindowStateChange:
            if self.windowState() == Qt.WindowState.WindowMinimized:
                self.hide()
                event.ignore()
                return
        super().changeEvent(event)
