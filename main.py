from __future__ import annotations

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from config import CONFIG
from ui.control_window import TranscriptionControlWindow


# Setup file logging for debug output
LOG_FILE = CONFIG.log_dir / "transcriber_debug.log"

def setup_logging():
    class Tee:
        def __init__(self, file_path, *streams):
            self.file = open(file_path, 'w', encoding='utf-8', buffering=1)  # Line buffering with UTF-8
            self.streams = streams
        
        def write(self, message):
            try:
                self.file.write(message)
            except UnicodeEncodeError:
                # Fallback: encode with error handling
                self.file.write(message.encode('utf-8', errors='replace').decode('utf-8'))
            for stream in self.streams:
                try:
                    stream.write(message)
                except UnicodeEncodeError:
                    # Fallback for console output too
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
    print(f"[DEBUG] Logging to {LOG_FILE}")


def suppress_qt_warning(msg_type: QtMsgType, context, message: str) -> None:
    # Suppress QFont point size warnings
    if "QFont" in message and "setPointSize" in message:
        return
    # Print other messages normally
    print(f"Qt {msg_type.name}: {message}", file=sys.stderr)


def main() -> None:
    # Setup file logging
    setup_logging()
    
    # Install custom message handler to suppress specific Qt warnings
    qInstallMessageHandler(suppress_qt_warning)
    
    print("[DEBUG] Starting application")
    app = QApplication([])
    print("[DEBUG] QApplication created")
    window = TranscriptionControlWindow()
    print("[DEBUG] TranscriptionControlWindow created")
    window.show()
    print("[DEBUG] Window shown")
    app.exec()
    print("[DEBUG] Application finished")


if __name__ == "__main__":
    main()
