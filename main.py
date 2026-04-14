from __future__ import annotations

import os
import sys
import math
from PySide6.QtWidgets import QApplication, QSplashScreen
from PySide6.QtCore import QTimer, Qt, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from config import CONFIG


if CONFIG.offline_mode:
    os.environ["ARGOS_CHUNK_TYPE"] = "MINISBD"
    os.environ["ARGOS_STANZA_AVAILABLE"] = "0"

from ui.control_window import ModelPreloadWorker, TranscriptionControlWindow


LOG_FILE = CONFIG.log_dir / "transcriber_debug.log"

def setup_logging():
    class Tee:
        def __init__(self, file_path, *streams):
            self.file = open(file_path, 'w', encoding='utf-8', buffering=1)
            self.streams = streams
        
        def write(self, message):
            try:
                self.file.write(message)
            except UnicodeEncodeError:
                self.file.write(message.encode('utf-8', errors='replace').decode('utf-8'))
            for stream in self.streams:
                try:
                    stream.write(message)
                except UnicodeEncodeError:
                    stream.write(message.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))
        
        def flush(self):
            self.file.flush()
            for stream in self.streams:
                stream.flush()
        
        def isatty(self):
            return False
    
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = Tee(LOG_FILE, original_stdout)
    sys.stderr = Tee(LOG_FILE, original_stderr)


def suppress_qt_warning(msg_type: QtMsgType, context, message: str) -> None:
    if "QFont" in message and "setPointSize" in message:
        return
    print(f"Qt {msg_type.name}: {message}", file=sys.stderr)


class LoadingSplashScreen(QSplashScreen):
    def __init__(self, pixmap: QPixmap):
        super().__init__(pixmap)
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(90)

    def _animate(self) -> None:
        self._frame = (self._frame + 1) % 12
        self.update()

    def stop_animation(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cx = self.width() - 52
        cy = self.height() - 38
        orbit_radius = 12
        dot_radius = 3.2
        dot_count = 12

        for i in range(dot_count):
            angle = (2 * math.pi * i) / dot_count
            x = cx + orbit_radius * math.cos(angle)
            y = cy + orbit_radius * math.sin(angle)
            distance = (self._frame - i) % dot_count
            alpha = max(45, 255 - (distance * 18))
            painter.setBrush(QColor(56, 189, 248, alpha))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(int(x - dot_radius), int(y - dot_radius), int(dot_radius * 2), int(dot_radius * 2))

        painter.end()


def main() -> None:
    setup_logging()

    qInstallMessageHandler(suppress_qt_warning)

    app = QApplication([])

    splash_pixmap = QPixmap(640, 300)
    splash_pixmap.fill(QColor("#0f172a"))
    painter = QPainter(splash_pixmap)
    painter.setPen(QColor("#38bdf8"))
    painter.setFont(QFont("Bahnschrift", 26, QFont.Weight.Bold))
    painter.drawText(24, 84, "Offline Caption Studio")
    painter.setPen(QColor("#cbd5e1"))
    painter.setFont(QFont("Bahnschrift", 11))
    painter.drawText(24, 116, "Launching offline speech pipeline...")
    painter.end()

    splash = LoadingSplashScreen(splash_pixmap)
    splash.show()
    splash.showMessage("Initializing interface...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, QColor("#e2e8f0"))
    app.processEvents()

    window = TranscriptionControlWindow()

    splash.showMessage("Loading default speech models...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, QColor("#e2e8f0"))
    app.processEvents()

    preload_languages = window.get_preload_languages_for_source()
    preload_languages = [lang for lang in preload_languages if lang in {"en", "hi"}]
    preload_ok = False
    preload_message = "No speech models available"

    if preload_languages:
        startup_done = {"finished": False, "failed": False, "message": ""}
        preload_worker = ModelPreloadWorker(window.controller, preload_languages)
        window.preload_thread = preload_worker

        def _on_finished(elapsed: float, languages: list[str]) -> None:
            window.on_model_preload_finished(elapsed, languages)
            startup_done["finished"] = True
            langs_text = ", ".join(lang.upper() for lang in languages)
            startup_done["message"] = f"Models ready ({elapsed:.1f}s) - {langs_text}"

        def _on_error(error_msg: str) -> None:
            window.on_model_preload_error(error_msg)
            startup_done["finished"] = True
            startup_done["failed"] = True
            startup_done["message"] = f"Model load failed: {error_msg}"

        preload_worker.finished_signal.connect(_on_finished)
        preload_worker.error_signal.connect(_on_error)
        preload_worker.start()

        while not startup_done["finished"]:
            app.processEvents()

        preload_ok = not startup_done["failed"] and window.models_ready
        preload_message = startup_done["message"] or ("Models ready" if preload_ok else "Model load failed")

    splash.showMessage(preload_message, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, QColor("#86efac" if preload_ok else "#fca5a5"))
    app.processEvents()

    window.show()
    splash.stop_animation()
    splash.finish(window)
    app.exec()


if __name__ == "__main__":
    main()
