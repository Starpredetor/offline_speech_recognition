from __future__ import annotations

from PySide6.QtWidgets import QApplication
from ui.control_window import TranscriptionControlWindow


def main() -> None:
    app = QApplication([])
    window = TranscriptionControlWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
