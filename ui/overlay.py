from __future__ import annotations

import sys
import json
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from PySide6.QtWidgets import QApplication, QWidget, QPushButton
from PySide6.QtCore import Qt, QTimer, QRect, QPoint
from PySide6.QtGui import QFont, QColor, QPainter, QTextOption, QGuiApplication

from config import CONFIG


@dataclass(slots=True)
class OverlayConfig:

    font_size: int = 16
    font_family: str = "Arial"
    text_color: QColor = None
    background_color: QColor = None
    opacity: float = 0.9
    padding: int = 10

    def __post_init__(self) -> None:
        if self.font_size <= 0:
            self.font_size = 16
        
        if self.text_color is None:
            self.text_color = QColor(255, 255, 255)
        if self.background_color is None:
            self.background_color = QColor(0, 0, 0)


class SubtitleOverlay(QWidget):

    def __init__(self, config: Optional[OverlayConfig] = None) -> None:
        super().__init__()
        self.config = config or OverlayConfig()
        self.current_text = ""
        self.target_window_rect: Optional[QRect] = None
        self.drag_position: Optional[QPoint] = None
        self.resize_edge = None
        self.resize_start_pos = None
        self.resize_start_geom = None
        self.resize_margin = 10
        self._loaded_saved_geometry = False

        self._setup_ui()
        self._load_preferences()

    def _setup_ui(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowOpacity(self.config.opacity)

        self.setGeometry(100, 100, 800, 150)
        
        self.setMouseTracking(True)

        self.close_btn = QPushButton("X", self)
        self.close_btn.setFixedSize(26, 22)
        self.close_btn.setStyleSheet(
            "QPushButton {"
            "background-color: rgba(200, 40, 40, 180);"
            "color: white;"
            "border: none;"
            "border-radius: 4px;"
            "font-weight: bold;"
            "}"
            "QPushButton:hover {"
            "background-color: rgba(220, 50, 50, 220);"
            "}"
        )
        self.close_btn.clicked.connect(self.hide)
        self.close_btn.raise_()
        self._reposition_controls()

    def _reposition_controls(self) -> None:
        self.close_btn.move(max(4, self.width() - self.close_btn.width() - 6), 6)
    
    def _get_prefs_file(self) -> Path:
        prefs_dir = CONFIG.app_data_dir / "prefs"
        prefs_dir.mkdir(parents=True, exist_ok=True)
        return prefs_dir / "overlay_prefs.json"
    
    def _load_preferences(self) -> None:
        prefs_file = self._get_prefs_file()
        if prefs_file.exists():
            try:
                with open(prefs_file, 'r') as f:
                    prefs = json.load(f)
                    self.setGeometry(
                        prefs.get('x', 100),
                        prefs.get('y', 100),
                        prefs.get('width', 800),
                        prefs.get('height', 150)
                    )
                    self._loaded_saved_geometry = True
            except Exception:
                pass
        self._ensure_visible_on_screen()

    def _ensure_visible_on_screen(self) -> None:
        screens = QGuiApplication.screens()
        if not screens:
            return

        current = self.frameGeometry()
        for screen in screens:
            if screen.availableGeometry().intersects(current):
                return

        primary = QGuiApplication.primaryScreen() or screens[0]
        area = primary.availableGeometry()
        width = min(max(self.width(), 420), max(420, area.width() - 40))
        height = min(max(self.height(), 100), max(100, area.height() // 3))
        x = area.x() + max(10, (area.width() - width) // 2)
        y = area.y() + max(10, area.height() - height - 60)
        self.setGeometry(x, y, width, height)
        self._save_preferences()
    
    def _save_preferences(self) -> None:
        try:
            prefs_file = self._get_prefs_file()
            geom = self.geometry()
            prefs = {
                'x': geom.x(),
                'y': geom.y(),
                'width': geom.width(),
                'height': geom.height()
            }
            with open(prefs_file, 'w') as f:
                json.dump(prefs, f)
        except Exception:
            pass

    def set_text(self, text: str) -> None:
        self.current_text = text
        self.update()

    def set_target_window_rect(self, rect: QRect) -> None:
        self.target_window_rect = rect
        if self._loaded_saved_geometry:
            return

        width = max(400, min(rect.width() - 40, 900))
        height = max(90, min(180, max(90, rect.height() // 5)))
        x = rect.x() + max(20, (rect.width() - width) // 2)
        y = rect.y() + max(20, rect.height() - height - 48)
        self.setGeometry(x, y, width, height)

    def _get_resize_edge(self, pos: QPoint) -> Optional[str]:
        rect = self.rect()
        margin = self.resize_margin
        
        edges = []
        if pos.x() < margin:
            edges.append('left')
        elif pos.x() > rect.width() - margin:
            edges.append('right')
            
        if pos.y() < margin:
            edges.append('top')
        elif pos.y() > rect.height() - margin:
            edges.append('bottom')
        
        return ''.join(edges) if edges else None

    def _update_cursor(self, edge: Optional[str]) -> None:
        if edge is None:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif edge in ('top', 'bottom'):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif edge in ('left', 'right'):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edge in ('topleft', 'bottomright'):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edge in ('topright', 'bottomleft'):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        painter.fillRect(self.rect(), self.config.background_color)

        font_size = max(self.config.font_size, 8) if self.config.font_size > 0 else 16
        font = QFont(self.config.font_family, font_size)
        painter.setFont(font)
        painter.setPen(self.config.text_color)

        text_rect = self.rect().adjusted(
            self.config.padding,
            self.config.padding,
            -self.config.padding,
            -self.config.padding,
        )

        text_option = QTextOption()
        text_option.setWrapMode(QTextOption.WrapMode.WordWrap)
        text_option.setAlignment(Qt.AlignmentFlag.AlignCenter)

        painter.drawText(text_rect, self.current_text, text_option)
        painter.end()

    def mousePressEvent(self, event) -> None:
        edge = self._get_resize_edge(event.pos())
        if edge:
            self.resize_edge = edge
            self.resize_start_pos = event.globalPosition().toPoint()
            self.resize_start_geom = self.geometry()
        else:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self.resize_edge:
            delta = event.globalPosition().toPoint() - self.resize_start_pos
            geom = QRect(self.resize_start_geom)
            
            if 'left' in self.resize_edge:
                new_width = geom.width() - delta.x()
                if new_width >= 200:
                    geom.setLeft(self.resize_start_geom.left() + delta.x())
            if 'right' in self.resize_edge:
                new_width = geom.width() + delta.x()
                if new_width >= 200:
                    geom.setWidth(new_width)
            if 'top' in self.resize_edge:
                new_height = geom.height() - delta.y()
                if new_height >= 50:
                    geom.setTop(self.resize_start_geom.top() + delta.y())
            if 'bottom' in self.resize_edge:
                new_height = geom.height() + delta.y()
                if new_height >= 50:
                    geom.setHeight(new_height)
            
            self.setGeometry(geom)
        elif hasattr(self, 'drag_position') and self.drag_position:
            self.move(event.globalPosition().toPoint() - self.drag_position)
        else:
            edge = self._get_resize_edge(event.pos())
            self._update_cursor(edge)
        
        event.accept()
    
    def mouseReleaseEvent(self, event) -> None:
        if self.resize_edge or self.drag_position:
            self._save_preferences()
        
        self.resize_edge = None
        self.resize_start_pos = None
        self.resize_start_geom = None
        self.drag_position = None
        event.accept()

    def resizeEvent(self, event) -> None:
        self._reposition_controls()
        super().resizeEvent(event)

    def show_overlay(self) -> None:
        self._ensure_visible_on_screen()
        self.show()
        self.raise_()


class OverlayManager:

    def __init__(self, config: Optional[OverlayConfig] = None) -> None:
        self.app: Optional[QApplication] = None
        self.overlay: Optional[SubtitleOverlay] = None
        self.config = config or OverlayConfig()
        self.update_timer: Optional[QTimer] = None

    def initialize(self) -> None:
        if self.app is None:
            app = QApplication.instance()
            if app is None:
                self.app = QApplication(sys.argv)
            else:
                self.app = app

        if self.overlay is None:
            self.overlay = SubtitleOverlay(self.config)
            self.overlay.show_overlay()

    def set_subtitle(self, text: str) -> None:
        if self.overlay is None:
            self.initialize()

        if self.overlay:
            self.overlay.set_text(text)

    def set_target_window(self, rect: QRect) -> None:
        if self.overlay:
            self.overlay.set_target_window_rect(rect)

    def show(self) -> None:
        if self.overlay is None:
            self.initialize()
        if self.overlay:
            self.overlay.show_overlay()

    def hide(self) -> None:
        if self.overlay:
            self.overlay.hide()

    def close(self) -> None:
        if self.overlay:
            self.overlay.close()
            self.overlay = None

    def clear_subtitle(self) -> None:
        if self.overlay is None:
            return
        self.overlay.set_text("")

    def process_events(self) -> None:
        if self.app:
            self.app.processEvents()
